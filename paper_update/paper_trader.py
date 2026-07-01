"""
paper_trader.py — 5/3/2 올웨더 페이퍼 트레이딩 엔진 (100만원 기준)
바이비트 대신 Kraken(미국 접속 가능) 시세로 가상 체결. 실거래 아님(가짜 돈).
한 틱 실행 → 가상 포트폴리오 갱신 → state.json 저장. (GitHub Actions로 30분마다 호출)

전략: 캐리 50% + 터틀 30% + 모멘텀 20% (백테스트와 동일 로직, 사이징은 단순화).
수익 계산: 수익률 기반(백테스트 방식) — 포지션 명목 × 시세변동 − 비용 + 캐리펀딩.
"""
import os, json, datetime as dt, urllib.request, math
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "state.json")

BASE_KRW   = 1_000_000        # 기준자본 100만원
W_CARRY, W_TURTLE, W_XMOM = 0.50, 0.30, 0.20
COINS = ["BTC", "ETH", "SOL", "XRP"]
KPAIR = {"BTC": "XBTUSD", "ETH": "ETHUSD", "SOL": "SOLUSD", "XRP": "XRPUSD"}
COST  = 0.0011                # 편도 비용(수수료+슬리피지) 0.11%
# 터틀 파라미터(4h봉 기준: 1일=6봉)
T_ENTRY, T_EXIT, T_ATR = 120, 60, 120     # 20일/10일/20일
T_STOP_N, T_ADD_N, T_MAXU, T_LEV = 2.0, 0.5, 6, 2.0
MA_DAYS = 200
X_LOOKBACK_D, X_REBAL_D = 20, 14
C_LEV, C_FUND_8H = 1.0, 0.0001            # 캐리 ×1, 모델 펀딩 0.01%/8h(양수 가정)


# ───────── 데이터 (Kraken) ─────────
def _get(url):
    return json.loads(urllib.request.urlopen(url, timeout=20).read())

def kraken_ohlc(pair, interval):
    r = _get(f"https://api.kraken.com/0/public/OHLC?pair={pair}&interval={interval}")
    if r.get("error"): return None
    key = [k for k in r["result"] if k != "last"][0]
    return r["result"][key]   # [[time,o,h,l,c,vwap,vol,cnt],...]

def usdkrw():
    try:
        return float(_get("https://api.exchangerate-api.com/v4/latest/USD")["rates"]["KRW"])
    except Exception:
        return 1380.0

def fetch_all():
    """4코인 4h/일봉 + 환율. 종가 리스트(USD)."""
    fx = usdkrw()
    h4, d1, price = {}, {}, {}
    for c in COINS:
        o4 = kraken_ohlc(KPAIR[c], 240)
        o1 = kraken_ohlc(KPAIR[c], 1440)
        h4[c] = [[float(x[2]), float(x[3]), float(x[4])] for x in o4]   # h,l,c
        d1[c] = [float(x[4]) for x in o1]                              # daily close
        price[c] = h4[c][-1][2] * fx                                   # 현재가 KRW
    return h4, d1, price, fx


# ───────── 지표 ─────────
def atr(hlc, n):
    trs = []
    for i in range(1, len(hlc)):
        h, l, _ = hlc[i]; pc = hlc[i-1][2]
        trs.append(max(h-l, abs(h-pc), abs(l-pc)))
    if len(trs) < n: return trs[-1] if trs else 0
    # EMA 근사
    k = 2/(n+1); a = trs[0]
    for t in trs[1:]: a = t*k + a*(1-k)
    return a

def sma(arr, n):
    if len(arr) < n: return sum(arr)/len(arr)
    return sum(arr[-n:])/n


# ───────── 전략 목표 (명목 KRW, 부호) ─────────
def turtle_targets(h4, d1, price, eq, st):
    coin_cap = eq * W_TURTLE / len(COINS)
    btc_price = d1["BTC"][-1]; btc_ma = sma(d1["BTC"], MA_DAYS)
    tgt = {}
    for c in COINS:
        hlc = h4[c]; closes4 = [x[2] for x in hlc]; highs = [x[0] for x in hlc]; lows = [x[1] for x in hlc]
        cur = price[c] / (price[c]/hlc[-1][2])  # = hlc[-1][2] USD... 사용은 KRW price[c]
        p = price[c]
        a = atr(hlc, T_ATR) * (price[c]/hlc[-1][2])   # ATR을 KRW로
        entry_hi = max(highs[-T_ENTRY-1:-1]); entry_lo = min(lows[-T_ENTRY-1:-1])
        exit_lo = min(lows[-T_EXIT-1:-1]); exit_hi = max(highs[-T_EXIT-1:-1])
        # USD→KRW scale for channels
        sc = price[c]/hlc[-1][2]
        entry_hi*=sc; entry_lo*=sc; exit_lo*=sc; exit_hi*=sc
        cur_high = hlc[-1][0]*sc; cur_low = hlc[-1][1]*sc
        s = st["turtle"].get(c, {"dir": None, "units": 0, "entry_atr": 0, "last_add": 0})
        d = 1 if s["dir"] == "long" else -1
        if s["dir"] and s["units"] > 0:
            stop = s["last_add"] - d * T_STOP_N * s["entry_atr"]
            exit_trig = (s["dir"]=="long" and cur_low<=exit_lo) or (s["dir"]=="short" and cur_high>=exit_hi)
            stop_trig = (s["dir"]=="long" and cur_low<=stop) or (s["dir"]=="short" and cur_high>=stop)
            if exit_trig or stop_trig:
                s = {"dir": None, "units": 0, "entry_atr": 0, "last_add": 0}
            elif s["units"] < T_MAXU and (p - s["last_add"])*d >= T_ADD_N*s["entry_atr"]:
                s["units"] += 1; s["last_add"] = p
        else:
            nd = "long" if cur_high>=entry_hi else ("short" if cur_low<=entry_lo else None)
            if nd=="long" and btc_price<btc_ma: nd=None
            if nd=="short" and btc_price>btc_ma: nd=None
            if nd:
                s = {"dir": nd, "units": 1, "entry_atr": a, "last_add": p}
        st["turtle"][c] = s
        if s["dir"] and s["units"]>0:
            scale = max(0.34, s["units"]/T_MAXU)
            tgt[c] = (1 if s["dir"]=="long" else -1) * coin_cap * T_LEV * scale
        else:
            tgt[c] = 0.0
    return tgt

def momentum_targets(d1, eq, st):
    sleeve = eq * W_XMOM
    now = dt.datetime.utcnow()
    mm = st["momentum"]
    due = (not mm.get("last_rebal")) or (now - dt.datetime.fromisoformat(mm["last_rebal"])).days >= X_REBAL_D
    if due:
        rets = {c: d1[c][-1]/d1[c][-1-X_LOOKBACK_D]-1 for c in COINS}
        order = sorted(rets, key=rets.get)
        mm["longs"] = order[-1:]; mm["shorts"] = order[:1]
        mm["last_rebal"] = now.isoformat(); mm["scores"] = {c: round(rets[c]*100,1) for c in COINS}
    tgt = {c: 0.0 for c in COINS}
    for c in mm.get("longs", []): tgt[c] = sleeve
    for c in mm.get("shorts", []): tgt[c] = -sleeve
    return tgt

def carry_funding(eq):
    """캐리는 델타중립(가격노출 0) → 펀딩 수익만. 명목 = 캐리자본 × lev."""
    notional = eq * W_CARRY * C_LEV
    return notional


# ───────── 포트폴리오 틱 ─────────
def load_state():
    if os.path.exists(STATE):
        s = json.load(open(STATE, encoding="utf-8"))
        s.setdefault("turtle_pos", {c: 0.0 for c in COINS})
        s.setdefault("momentum_pos", {c: 0.0 for c in COINS})
        s.setdefault("eng_pnl", {"carry": 0.0, "turtle": 0.0, "momentum": 0.0})
        return s
    return {"equity": BASE_KRW, "prev_price": {}, "positions": {c: 0.0 for c in COINS},
            "turtle_pos": {c: 0.0 for c in COINS}, "momentum_pos": {c: 0.0 for c in COINS},
            "eng_pnl": {"carry": 0.0, "turtle": 0.0, "momentum": 0.0},
            "turtle": {}, "momentum": {}, "carry": {}, "trades": [], "history": [], "last_tick": None}

def save_state(s):
    json.dump(s, open(STATE, "w", encoding="utf-8"), indent=1, ensure_ascii=False)

def tick():
    st = load_state()
    h4, d1, price, fx = fetch_all()
    now = dt.datetime.utcnow()
    eq = st["equity"]

    # 1) 이전 포지션의 시세변동 수익 — 엔진별 분리
    if st["prev_price"]:
        t_ret = x_ret = 0.0
        for c in COINS:
            if c in st["prev_price"] and st["prev_price"][c] > 0:
                pr = price[c]/st["prev_price"][c] - 1
                t_ret += st["turtle_pos"].get(c, 0.0) * pr
                x_ret += st["momentum_pos"].get(c, 0.0) * pr
        # 캐리: 델타중립 → 펀딩 수익만
        hrs = 0.5
        if st["last_tick"]:
            hrs = min(8, (now - dt.datetime.fromisoformat(st["last_tick"])).total_seconds()/3600)
        c_ret = carry_funding(eq) * C_FUND_8H * (hrs/8)
        st["eng_pnl"]["turtle"] += t_ret; st["eng_pnl"]["momentum"] += x_ret; st["eng_pnl"]["carry"] += c_ret
        eq += t_ret + x_ret + c_ret

    # 2) 새 목표 계산 (엔진별)
    tt = turtle_targets(h4, d1, price, eq, st)
    xm = momentum_targets(d1, eq, st)
    net = {c: tt.get(c,0)+xm.get(c,0) for c in COINS}

    # 3) 목표 변화 → 거래비용(엔진별 차감) + 매매일지
    for c in COINS:
        for eng, newp, oldmap in [("turtle", tt, st["turtle_pos"]), ("momentum", xm, st["momentum_pos"])]:
            old = oldmap.get(c, 0.0); nv = newp.get(c, 0.0)
            if abs(nv-old) > eq*0.005:
                cost = abs(nv-old)*COST
                eq -= cost; st["eng_pnl"][eng] -= cost
                if eng == "momentum" or abs(nv-old) > eq*0.02:
                    st["trades"].append({"t": now.strftime("%Y-%m-%d %H:%M"), "coin": c, "eng": eng,
                                         "act": ("롱↑" if nv>0 else "숏↑") if abs(nv)>abs(old) else "축소/청산",
                                         "notional": round(nv), "price": round(price[c])})
    st["trades"] = st["trades"][-100:]

    # 4) 상태 저장
    st["turtle_pos"] = tt; st["momentum_pos"] = xm
    st["positions"] = net
    st["prev_price"] = price
    st["equity"] = eq
    st["last_tick"] = now.isoformat()
    st["fx"] = fx
    st["history"].append([now.strftime("%Y-%m-%d %H:%M"), round(eq)])
    st["history"] = st["history"][-2000:]
    save_state(st)
    return st, price


if __name__ == "__main__":
    st, price = tick()
    ret = (st["equity"]/BASE_KRW-1)*100
    print(f"=== 페이퍼 트레이딩 틱 완료 ({dt.datetime.utcnow():%Y-%m-%d %H:%M} UTC) ===")
    print(f"  자산: {st['equity']:,.0f}원  (기준 {BASE_KRW:,}원, 수익률 {ret:+.2f}%)")
    print(f"  환율 USDKRW {st['fx']:.0f}")
    print(f"  현재 포지션(명목 KRW):")
    for c in COINS:
        p = st["positions"][c]
        if abs(p) > 1: print(f"    {c}: {p:+,.0f}원  ({'롱' if p>0 else '숏'})  현재가 {price[c]:,.0f}원")
    print(f"  모멘텀: 롱{st['momentum'].get('longs')} 숏{st['momentum'].get('shorts')}  점수{st['momentum'].get('scores')}")
    ep = st["eng_pnl"]; sleeve = {"carry": BASE_KRW*W_CARRY, "turtle": BASE_KRW*W_TURTLE, "momentum": BASE_KRW*W_XMOM}
    print(f"  전략별 손익:")
    for e in ["carry", "turtle", "momentum"]:
        print(f"    {e}: {ep[e]:+,.0f}원  ({ep[e]/sleeve[e]*100:+.2f}% / 배분 {sleeve[e]:,.0f}원)")
    print(f"  누적 거래 {len(st['trades'])}건, 자산기록 {len(st['history'])}개")
