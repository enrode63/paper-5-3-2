"""
dashboard.py — state.json → index.html (웹 대시보드 생성)
GitHub Pages로 서빙. 자산곡선(Chart.js) + 포지션 + 매매일지 + 통계.
"""
import os, json, datetime as dt

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "state.json")
OUT = os.path.join(HERE, "index.html")
BASE = 1_000_000


def build():
    st = json.load(open(STATE, encoding="utf-8"))
    eq = st["equity"]; ret = (eq/BASE-1)*100
    hist = st["history"]
    labels = [h[0] for h in hist]; values = [h[1] for h in hist]
    # 최대낙폭
    peak = -1e9; mdd = 0
    for v in values:
        peak = max(peak, v); mdd = min(mdd, v/peak-1)
    mdd *= 100
    color = "#16c784" if ret >= 0 else "#ea3943"

    # 포지션 행
    pos_rows = ""
    for c, p in st["positions"].items():
        if abs(p) > 1:
            side = "롱 LONG" if p > 0 else "숏 SHORT"
            sc = "#16c784" if p > 0 else "#ea3943"
            pos_rows += f"<tr><td>{c}</td><td style='color:{sc}'>{side}</td><td>{p:+,.0f}원</td></tr>"
    if not pos_rows:
        pos_rows = "<tr><td colspan=3 style='color:#888'>포지션 없음</td></tr>"

    # 매매일지 (최근 15)
    tr_rows = ""
    for t in reversed(st["trades"][-15:]):
        tr_rows += f"<tr><td>{t['t']}</td><td>{t['coin']}</td><td>{t['act']}</td><td>{t['notional']:+,}원</td></tr>"
    if not tr_rows:
        tr_rows = "<tr><td colspan=4 style='color:#888'>거래 없음</td></tr>"

    # 전략별 손익
    ep = st.get("eng_pnl", {"carry":0,"turtle":0,"momentum":0})
    sleeve = {"carry": BASE*0.50, "turtle": BASE*0.30, "momentum": BASE*0.20}
    names = {"carry": "캐리 50%", "turtle": "터틀 30%", "momentum": "모멘텀 20%"}
    eng_rows = ""
    for e in ["carry", "turtle", "momentum"]:
        pnl = ep.get(e, 0); pct = pnl/sleeve[e]*100 if sleeve[e] else 0
        ec = "#16c784" if pnl >= 0 else "#ea3943"
        eng_rows += f"<tr><td>{names[e]}</td><td style='color:{ec}'>{pnl:+,.0f}원</td><td style='color:{ec}'>{pct:+.2f}%</td></tr>"

    mm = st.get("momentum", {})
    scores = mm.get("scores", {})
    score_txt = "  ".join(f"{k} {v:+.1f}%" for k, v in scores.items()) if scores else "-"
    updated = st.get("last_tick", "")[:16].replace("T", " ")

    html = f"""<!DOCTYPE html><html lang=ko><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>올웨더 5/3/2 페이퍼 트레이딩</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
<style>
body{{font-family:-apple-system,Segoe UI,Roboto,sans-serif;background:#0d1117;color:#e6edf3;margin:0;padding:16px;max-width:820px;margin:auto}}
h1{{font-size:20px}} .sub{{color:#8b949e;font-size:13px}}
.cards{{display:flex;gap:12px;flex-wrap:wrap;margin:16px 0}}
.card{{background:#161b22;border:1px solid #30363d;border-radius:10px;padding:14px 18px;flex:1;min-width:130px}}
.card .v{{font-size:22px;font-weight:700}} .card .l{{color:#8b949e;font-size:12px}}
table{{width:100%;border-collapse:collapse;margin:8px 0 20px}}
th,td{{text-align:left;padding:8px 10px;border-bottom:1px solid #21262d;font-size:14px}}
th{{color:#8b949e;font-weight:500}}
.box{{background:#161b22;border:1px solid #30363d;border-radius:10px;padding:12px 16px;margin-bottom:16px}}
canvas{{max-height:260px}}
</style></head><body>
<h1>🌦️ 올웨더 5/3/2 페이퍼 트레이딩</h1>
<div class=sub>캐리 50% · 터틀 30% · 모멘텀 20% | 가짜 돈 시뮬레이션 (Kraken 시세) | 갱신 {updated} UTC</div>
<div class=cards>
  <div class=card><div class=l>현재 자산</div><div class=v>{eq:,.0f}원</div></div>
  <div class=card><div class=l>수익률</div><div class=v style='color:{color}'>{ret:+.2f}%</div></div>
  <div class=card><div class=l>최대낙폭</div><div class=v>{mdd:.2f}%</div></div>
  <div class=card><div class=l>기준자본</div><div class=v>{BASE:,}원</div></div>
</div>
<div class=box><canvas id=eq></canvas></div>
<h3>전략별 수익률</h3>
<table><tr><th>전략</th><th>손익</th><th>수익률</th></tr>{eng_rows}</table>
<h3>현재 포지션</h3>
<table><tr><th>종목</th><th>방향</th><th>명목</th></tr>{pos_rows}</table>
<div class=sub>모멘텀 순위(20일): {score_txt}</div>
<h3>최근 매매일지</h3>
<table><tr><th>시각(UTC)</th><th>종목</th><th>동작</th><th>명목</th></tr>{tr_rows}</table>
<div class=sub>⚠️ 페이퍼 트레이딩(가짜 돈) · 학습·검증용 · 실거래 아님. 30분마다 자동 갱신.</div>
<script>
new Chart(document.getElementById('eq'),{{type:'line',
 data:{{labels:{json.dumps(labels)},datasets:[{{label:'자산(원)',data:{json.dumps(values)},
   borderColor:'{color}',backgroundColor:'rgba(22,199,132,.1)',fill:true,tension:.2,pointRadius:0,borderWidth:2}}]}},
 options:{{plugins:{{legend:{{display:false}}}},scales:{{x:{{ticks:{{maxTicksLimit:6,color:'#8b949e'}},grid:{{display:false}}}},
   y:{{ticks:{{color:'#8b949e'}},grid:{{color:'#21262d'}}}}}}}}}});
</script></body></html>"""
    open(OUT, "w", encoding="utf-8").write(html)
    print(f"[dashboard] 생성: {OUT}  (자산 {eq:,.0f}원, 수익률 {ret:+.2f}%)")


if __name__ == "__main__":
    build()
