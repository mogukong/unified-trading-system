#!/usr/bin/env python3
"""
复盘分析脚本 - 使用新指标函数分析指定币种
用法: python3 review_coins.py [币种1] [币种2] ...
默认: BEATUSDT VELVETUSDT HAIOUSDT AIOUSDT
"""
import sys, os, json, time
from urllib.request import Request, urlopen
from urllib.error import URLError

# 加载API密钥
def load_keys():
    env_path = os.path.join(os.path.dirname(__file__), '..', '.env')
    if not os.path.exists(env_path):
        env_path = os.path.join(os.path.dirname(__file__), '.env')
    key, secret = "", ""
    if os.path.exists(env_path):
        for line in open(env_path):
            line = line.strip()
            if line.startswith('BINANCE_API_KEY='):
                key = line.split('=', 1)[1].strip().strip('"').strip("'")
            elif line.startswith('BINANCE_API_SECRET='):
                secret = line.split('=', 1)[1].strip().strip('"').strip("'")
    return key, secret

BASE_URL = "https://fapi.binance.com"

def fetch_json(url, timeout=10):
    try:
        req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except Exception as e:
        return None

# ============================================================
# 技术指标函数 (直接内联，不依赖engine的import链)
# ============================================================
def calc_bollinger_bands(klines_raw, period=20, std_dev=2.0):
    result = {}
    if not klines_raw or len(klines_raw) < period: return result
    closes = [float(k[4]) for k in klines_raw]
    sma = sum(closes[-period:]) / period
    variance = sum((c - sma) ** 2 for c in closes[-period:]) / period
    std = variance ** 0.5
    upper = sma + std_dev * std
    lower = sma - std_dev * std
    bandwidth = (upper - lower) / sma * 100 if sma > 0 else 0
    current = closes[-1]
    pct_b = (current - lower) / (upper - lower) if (upper - lower) > 0 else 0.5
    squeeze = False
    if len(closes) >= 60:
        hbw = []
        for i in range(20, len(closes)):
            ch = closes[i-20:i]; s = sum(ch)/20; v = sum((c-s)**2 for c in ch)/20; st = v**0.5
            bw = (s+2*st-(s-2*st))/s*100 if s>0 else 0; hbw.append(bw)
        hbw.sort(); idx = int(len(hbw)*0.2)
        squeeze = bandwidth < hbw[idx] if hbw else False
    return {"bb_upper":upper, "bb_middle":sma, "bb_lower":lower,
            "bb_bandwidth":round(bandwidth,2), "bb_pct_b":round(pct_b,3), "bb_squeeze":squeeze}

def calc_ema_multi(klines_raw):
    result = {}
    if not klines_raw or len(klines_raw) < 55: return result
    closes = [float(k[4]) for k in klines_raw]
    def _ema(data, period):
        m = 2/(period+1); e = data[0]
        for p in data[1:]: e = (p-e)*m+e
        return e
    e9 = _ema(closes,9); e21 = _ema(closes,21); e55 = _ema(closes,55)
    if e9>e21>e55: trend,align = "bullish","多头排列"
    elif e9<e21<e55: trend,align = "bearish","空头排列"
    elif e9>e21 and e21<e55: trend,align = "recovering","金叉形成中"
    elif e9<e21 and e21>e55: trend,align = "weakening","死叉形成中"
    else: trend,align = "neutral","均线纠缠"
    spread = (e9-e55)/e55*100 if e55>0 else 0
    slope = 0
    if len(closes)>=58:
        e21p = _ema(closes[:-3],21)
        slope = (e21-e21p)/e21p*100 if e21p>0 else 0
    return {"ema9":e9,"ema21":e21,"ema55":e55,"ema_trend":trend,"ema_align":align,
            "ema_spread":round(spread,2),"ema_slope":round(slope,3)}

def calc_macd(klines_raw, fast=12, slow=26, signal=9):
    result = {}
    if not klines_raw or len(klines_raw) < slow+signal: return result
    closes = [float(k[4]) for k in klines_raw]
    def _ema_s(data, period):
        m=2/(period+1); v=[data[0]]
        for p in data[1:]: v.append((p-v[-1])*m+v[-1])
        return v
    ef = _ema_s(closes,fast); es = _ema_s(closes,slow)
    ml = [f-s for f,s in zip(ef,es)]; sl = _ema_s(ml,signal)
    hl = [m-s for m,s in zip(ml,sl)]
    cm,cs,ch = ml[-1],sl[-1],hl[-1]; ph = hl[-2] if len(hl)>=2 else 0
    if len(ml)>=2 and len(sl)>=2:
        pm,ps = ml[-2],sl[-2]
        if pm<=ps and cm>cs: cross="golden_cross"
        elif pm>=ps and cm<cs: cross="death_cross"
        else: cross="none"
    else: cross="none"
    if cm>0 and cs>0: trend="bullish_zone"
    elif cm<0 and cs<0: trend="bearish_zone"
    elif cm>cs: trend="turning_bullish"
    else: trend="turning_bearish"
    if len(hl)>=3:
        h1,h2,h3=hl[-3],hl[-2],hl[-1]
        if abs(h3)>abs(h2)>abs(h1): mom="expanding"
        elif abs(h3)<abs(h2)<abs(h1): mom="contracting"
        else: mom="mixed"
    else: mom="unknown"
    if abs(ch)<abs(ph)*0.5: ha="flattening"
    elif (ph<0 and ch>ph) or (ph>0 and ch<ph): ha="reversing"
    else: ha="continuing"
    return {"macd_line":round(cm,6),"signal_line":round(cs,6),"histogram":round(ch,6),
            "macd_cross":cross,"macd_trend":trend,"histogram_momentum":mom,"histogram_action":ha}

def calc_volume_quality(klines_raw):
    result = {}
    if not klines_raw or len(klines_raw)<20: return result
    vols=[float(k[5]) for k in klines_raw]; closes=[float(k[4]) for k in klines_raw]
    opens=[float(k[1]) for k in klines_raw]; highs=[float(k[2]) for k in klines_raw]
    ma20=sum(vols[-20:])/20; cv=vols[-1]; vr=cv/ma20 if ma20>0 else 1
    r5=sum(vols[-5:])/5; p5=sum(vols[-10:-5])/5
    trend="increasing" if r5>p5*1.2 else "decreasing" if r5<p5*0.8 else "stable"
    bv=0; tv=0
    for i in range(-min(20,len(vols)),0):
        if closes[i]>=opens[i]: bv+=vols[i]
        tv+=vols[i]
    bp=bv/tv*100 if tv>0 else 50
    rh=max(highs[-20:-1]) if len(highs)>=20 else 0
    bo=vr>2.0 and closes[-1]>rh
    ph=max(closes[-10:]); pl=min(closes[-10:]); vh=max(vols[-10:])
    div="none"
    if closes[-1]>=ph*0.99 and vols[-1]<vh*0.6: div="bearish_top"
    elif closes[-1]<=pl*1.01 and vols[-1]<vh*0.6: div="bullish_bottom"
    qs=50
    if vr>2.0: qs+=20
    elif vr>1.5: qs+=10
    elif vr<0.5: qs-=15
    if bp>60: qs+=15
    elif bp>55: qs+=5
    elif bp<40: qs-=10
    if bo: qs+=15
    if div=="bearish_top": qs-=15
    elif div=="bullish_bottom": qs+=10
    return {"vol_ma20":ma20,"vol_ratio_20":round(vr,2),"vol_trend":trend,
            "vol_buy_pct":round(bp,1),"vol_breakout":bo,"vol_divergence":div,
            "vol_quality_score":max(0,min(100,qs))}

def calc_support_resistance(klines_raw):
    result = {}
    if not klines_raw or len(klines_raw)<30: return result
    highs=[float(k[2]) for k in klines_raw]; lows=[float(k[3]) for k in klines_raw]
    closes=[float(k[4]) for k in klines_raw]; cur=closes[-1]
    sw_h=[]; sw_l=[]; lb=3
    for i in range(lb,len(highs)-lb):
        if all(highs[i]>=highs[i+j] for j in range(-lb,lb+1) if j!=0): sw_h.append(highs[i])
        if all(lows[i]<=lows[i+j] for j in range(-lb,lb+1) if j!=0): sw_l.append(lows[i])
    pr=max(highs)-min(lows)
    if pr<=0: return result
    bc=20; bs=pr/bc; bins=[0]*bc
    for i in range(len(closes)):
        idx=int((closes[i]-min(lows))/bs); idx=min(idx,bc-1); bins[idx]+=1
    mi=bins.index(max(bins)); dc=min(lows)+(mi+0.5)*bs
    du=min(lows)+(mi+1)*bs; dl=min(lows)+mi*bs
    above=sorted([h for h in sw_h if h>cur])
    below=sorted([l for l in sw_l if l<cur], reverse=True)
    rc=above[:3]+[du] if du>cur else above[:3]; rc=sorted(set(rc))
    r1=rc[0] if rc else max(highs); r2=rc[1] if len(rc)>1 else r1*1.05
    sc=below[:3]+[dl] if dl<cur else below[:3]; sc=sorted(set(sc),reverse=True)
    s1=sc[0] if sc else min(lows); s2=sc[1] if len(sc)>1 else s1*0.95
    pivot=(highs[-1]+lows[-1]+closes[-1])/3
    sr=r1-s1; pp=((cur-s1)/sr*100) if sr>0 else 50
    rp=sr/cur*100 if cur>0 else 0
    return {"sr_resistance":r1,"sr_support":s1,"sr_resistance_2":r2,"sr_support_2":s2,
            "sr_pivot":round(pivot,6),"sr_range_pct":round(rp,2),"sr_position":round(pp,1),
            "sr_dense_center":round(dc,6)}

def calc_volume_pattern(klines_raw):
    result = {}
    if not klines_raw or len(klines_raw)<20: return result
    vols=[float(k[5]) for k in klines_raw]; closes=[float(k[4]) for k in klines_raw]
    opens=[float(k[1]) for k in klines_raw]; highs=[float(k[2]) for k in klines_raw]
    lows=[float(k[3]) for k in klines_raw]
    ma20=sum(vols[-20:])/20; ma5=sum(vols[-5:])/5; vr=vols[-1]/ma20 if ma20>0 else 1
    p5c=(closes[-1]-closes[-5])/closes[-5]*100 if closes[-5]>0 else 0
    h10=max(highs[-10:]); l10=min(lows[-10:]); pr=h10-l10
    pp=(closes[-1]-l10)/pr*100 if pr>0 else 50
    vt="up" if ma5>ma20*1.2 else "down" if ma5<ma20*0.8 else "flat"
    pat="normal"; health="neutral"; sig="none"
    if vr>2.0 and pp>80: pat="vol_breakout"; health="strong"; sig="bullish"
    elif p5c<-2 and ma5<ma20*0.7: pat="vol_shrink_pullback"; health="healthy"; sig="buy_dip"
    elif p5c>3 and vr>1.5: pat="vol_expansion"; health="strong"; sig="bullish"
    elif pp>80 and vr<0.7: pat="vol_divergence_top"; health="warning"; sig="bearish_divergence"
    elif pp<20 and vr<0.7: pat="vol_divergence_bottom"; health="accumulation"; sig="bullish_divergence"
    elif vr>3.0 and p5c<-5: pat="vol_panic"; health="danger"; sig="bearish"
    elif vr<0.5 and abs(p5c)<2: pat="vol_dry"; health="stagnant"; sig="wait"
    return {"vol_pattern":pat,"vol_health":health,"vol_signal":sig,"vol_ratio_current":round(vr,2),
            "vol_price_position":round(pp,1),"vol_trend_5":vt}

def detect_consolidation_box(klines_raw, min_touches=3):
    result = {}
    if not klines_raw or len(klines_raw)<20: return result
    highs=[float(k[2]) for k in klines_raw]; lows=[float(k[3]) for k in klines_raw]
    closes=[float(k[4]) for k in klines_raw]; cur=closes[-1]
    lb=min(30,len(klines_raw)); rh=highs[-lb:]; rl=lows[-lb:]
    sn=max(2,len(rh)//5); bh=sum(sorted(rh,reverse=True)[:sn])/sn
    bn=max(2,len(rl)//5); bl=sum(sorted(rl)[:bn])/bn
    bm=(bh+bl)/2; bw=bh-bl; bwp=bw/bm*100 if bm>0 else 0
    tol=bw*0.1; ut=sum(1 for h in rh if abs(h-bh)<tol)
    lt2=sum(1 for l in rl if abs(l-bl)<tol); tt=ut+lt2
    bp=(cur-bl)/bw*100 if bw>0 else 50
    if cur>bh*1.01: st="breaking_up"
    elif cur<bl*0.99: st="breaking_down"
    elif tt>=min_touches*2: st="confirmed"
    elif tt>=min_touches: st="forming"
    else: st="not_detected"
    q=min(100,tt*15+(20 if 3<bwp<15 else 0))
    return {"box_high":round(bh,6),"box_low":round(bl,6),"box_mid":round(bm,6),
            "box_width_pct":round(bwp,2),"box_upper_touches":ut,"box_lower_touches":lt2,
            "box_duration":lb,"box_position":round(bp,1),"box_status":st,"box_quality":q}

# ============================================================
# 分析报告生成
# ============================================================
def generate_report(sym, kline_data, funding_data, oi_data, ls_data, taker_data):
    """生成结构化分析报告"""
    lines = []
    close = kline_data.get("close", 0)
    
    lines.append(f"{'='*50}")
    lines.append(f"📊 {sym} 综合技术分析报告")
    lines.append(f"{'='*50}")
    lines.append(f"当前价格: {close}")
    lines.append(f"24h涨幅: {kline_data.get('price_change_24h', 0):.2f}%")
    lines.append(f"4h涨幅: {kline_data.get('price_change_4h', 0):.2f}%")
    lines.append("")
    
    # 1. EMA趋势
    ema_trend = kline_data.get("ema_trend", "N/A")
    ema_align = kline_data.get("ema_align", "N/A")
    ema_spread = kline_data.get("ema_spread", 0)
    ema_slope = kline_data.get("ema_slope", 0)
    ema9 = kline_data.get("ema9", 0)
    ema21 = kline_data.get("ema21", 0)
    ema55 = kline_data.get("ema55", 0)
    lines.append(f"📈 EMA多周期趋势")
    lines.append(f"   EMA9={ema9:.4f} | EMA21={ema21:.4f} | EMA55={ema55:.4f}")
    lines.append(f"   排列: {ema_align} | 离散: {ema_spread:+.2f}% | 斜率: {ema_slope:+.3f}%")
    if ema_trend == "bullish":
        lines.append(f"   ✅ 多头排列 — 趋势向上")
    elif ema_trend == "bearish":
        lines.append(f"   🔴 空头排列 — 趋势向下")
    elif ema_trend == "recovering":
        lines.append(f"   🟡 金叉形成中 — 趋势转多")
    elif ema_trend == "weakening":
        lines.append(f"   ⚠️ 死叉形成中 — 趋势转弱")
    lines.append("")
    
    # 2. MACD
    macd_line = kline_data.get("macd_line", 0)
    signal_line = kline_data.get("signal_line", 0)
    histogram = kline_data.get("histogram", 0)
    macd_cross = kline_data.get("macd_cross", "none")
    macd_trend = kline_data.get("macd_trend", "neutral")
    hist_action = kline_data.get("histogram_action", "continuing")
    hist_mom = kline_data.get("histogram_momentum", "unknown")
    lines.append(f"📊 MACD")
    lines.append(f"   DIF={macd_line:.6f} | DEA={signal_line:.6f} | 柱={histogram:.6f}")
    lines.append(f"   趋势: {macd_trend} | 动量: {hist_mom} | 走势: {hist_action}")
    if macd_cross == "golden_cross":
        lines.append(f"   ✅ MACD金叉 — 做多信号")
    elif macd_cross == "death_cross":
        lines.append(f"   🔴 MACD死叉 — 做空信号")
    if hist_action == "flattening":
        lines.append(f"   ⚡ 柱状图走平 — 变盘在即")
    lines.append("")
    
    # 3. 布林带
    bb_upper = kline_data.get("bb_upper", 0)
    bb_middle = kline_data.get("bb_middle", 0)
    bb_lower = kline_data.get("bb_lower", 0)
    bb_pct_b = kline_data.get("bb_pct_b", 0.5)
    bb_bw = kline_data.get("bb_bandwidth", 0)
    bb_sq = kline_data.get("bb_squeeze", False)
    lines.append(f"📉 布林带")
    lines.append(f"   上轨={bb_upper:.4f} | 中轨={bb_middle:.4f} | 下轨={bb_lower:.4f}")
    lines.append(f"   %B={bb_pct_b:.3f} | 带宽={bb_bw:.2f}% | 挤压={'是' if bb_sq else '否'}")
    if bb_pct_b > 0.9:
        lines.append(f"   🔴 触及上轨 — 超买")
    elif bb_pct_b < 0.1:
        lines.append(f"   ✅ 触及下轨 — 超卖")
    elif 0.4 < bb_pct_b < 0.6:
        lines.append(f"   ⚪ 中轨附近 — 中性")
    if bb_sq:
        lines.append(f"   ⚡ 布林带挤压 — 即将变盘!")
    lines.append("")
    
    # 4. 成交量
    vol_pat = kline_data.get("vol_pattern", "N/A")
    vol_health = kline_data.get("vol_health", "N/A")
    vol_ratio = kline_data.get("vol_ratio_current", 1)
    vol_buy = kline_data.get("vol_buy_pct", 50)
    vol_bp = kline_data.get("vol_breakout", False)
    vol_div = kline_data.get("vol_divergence", "none")
    vol_qs = kline_data.get("vol_quality_score", 50)
    lines.append(f"📦 成交量质量")
    lines.append(f"   量比={vol_ratio} | 买盘={vol_buy}% | 质量分={vol_qs}")
    lines.append(f"   模式: {vol_pat} | 健康: {vol_health}")
    if vol_bp:
        lines.append(f"   🔥 放量突破!")
    if vol_div == "bearish_top":
        lines.append(f"   ⚠️ 量价顶背离 — 注意反转")
    elif vol_div == "bullish_bottom":
        lines.append(f"   ✅ 底背离 — 抛压衰竭")
    lines.append("")
    
    # 5. 支撑阻力
    sr_res = kline_data.get("sr_resistance", 0)
    sr_sup = kline_data.get("sr_support", 0)
    sr_res2 = kline_data.get("sr_resistance_2", 0)
    sr_sup2 = kline_data.get("sr_support_2", 0)
    sr_pos = kline_data.get("sr_position", 50)
    sr_range = kline_data.get("sr_range_pct", 0)
    lines.append(f"🛡️ 支撑阻力")
    lines.append(f"   阻力1: {sr_res:.4f} | 阻力2: {sr_res2:.4f}")
    lines.append(f"   支撑1: {sr_sup:.4f} | 支撑2: {sr_sup2:.4f}")
    lines.append(f"   区间宽度: {sr_range}% | 位置: {sr_pos}%")
    if sr_pos > 80:
        lines.append(f"   🔴 接近阻力位 — 注意回落风险")
    elif sr_pos < 20:
        lines.append(f"   ✅ 接近支撑位 — 潜在买入区")
    lines.append("")
    
    # 6. 箱体
    box_st = kline_data.get("box_status", "not_detected")
    box_pos = kline_data.get("box_position", 50)
    box_high = kline_data.get("box_high", 0)
    box_low = kline_data.get("box_low", 0)
    box_wp = kline_data.get("box_width_pct", 0)
    lines.append(f"🔲 箱体/整理区间")
    if box_st != "not_detected":
        lines.append(f"   上沿={box_high:.4f} | 下沿={box_low:.4f} | 宽度={box_wp}%")
        lines.append(f"   状态: {box_st} | 位置: {box_pos}%")
        if box_st == "breaking_up":
            lines.append(f"   🔥 向上突破!")
        elif box_st == "breaking_down":
            lines.append(f"   🔴 向下破位!")
    else:
        lines.append(f"   未检测到明显箱体")
    lines.append("")
    
    # 7. RSI + 多空比 + 资金费率
    rsi = kline_data.get("rsi", 50)
    long_ratio = ls_data.get("long_ratio", 50)
    funding = funding_data.get("rate", 0)
    oi_change = oi_data.get("oi_change_pct", 0)
    lines.append(f"📋 其他指标")
    lines.append(f"   RSI: {rsi:.1f} | 多头占比: {long_ratio:.1f}% | 费率: {funding*100:.4f}%")
    lines.append(f"   OI变化: {oi_change:+.2f}%")
    if rsi > 75:
        lines.append(f"   ⚠️ RSI超买")
    elif rsi < 30:
        lines.append(f"   ✅ RSI超卖")
    lines.append("")
    
    return "\n".join(lines)


def analyze_coin(sym):
    """分析单个币种"""
    print(f"\n⏳ 正在获取 {sym} 数据...")
    
    # 获取数据 (完整模式)
    klines_1h = fetch_json(f"{BASE_URL}/fapi/v1/klines?symbol={sym}&interval=1h&limit=100")
    klines_4h = fetch_json(f"{BASE_URL}/fapi/v1/klines?symbol={sym}&interval=4h&limit=30")
    funding = fetch_json(f"{BASE_URL}/fapi/v1/fundingRate?symbol={sym}&limit=8")
    oi_hist = fetch_json(f"https://fapi.binance.com/futures/data/openInterestHist?symbol={sym}&period=1h&limit=24")
    top_ls = fetch_json(f"https://fapi.binance.com/futures/data/topLongShortPositionRatio?symbol={sym}&period=1h&limit=24")
    taker = fetch_json(f"https://fapi.binance.com/futures/data/takerlongshortRatio?symbol={sym}&period=1h&limit=24")
    
    if not klines_1h or len(klines_1h) < 20:
        print(f"❌ {sym} K线数据不足")
        return None
    
    # 计算基础指标
    close_prices = [float(k[4]) for k in klines_1h]
    kline_data = {"close": close_prices[-1]}
    
    # RSI
    if len(close_prices) >= 14:
        gains=[]; losses=[]
        for i in range(1,len(close_prices)):
            d=close_prices[i]-close_prices[i-1]
            gains.append(d if d>0 else 0); losses.append(abs(d) if d<0 else 0)
        ag=sum(gains[-14:])/14; al=sum(losses[-14:])/14
        kline_data["rsi"] = 100-(100/(1+ag/al)) if al>0 else 100
    
    # 涨跌幅
    if len(close_prices)>=2:
        kline_data["price_change_24h"]=(close_prices[-1]-close_prices[0])/close_prices[0]*100
    if len(close_prices)>=4:
        kline_data["price_change_4h"]=(close_prices[-1]-close_prices[-4])/close_prices[-4]*100
    
    # EMA20/50 (旧版兼容)
    if len(close_prices)>=20:
        m=2/21; e=close_prices[0]
        for p in close_prices[1:]: e=(p-e)*m+e
        kline_data["ema20"]=e
    if len(close_prices)>=50:
        m=2/51; e=close_prices[0]
        for p in close_prices[1:]: e=(p-e)*m+e
        kline_data["ema50"]=e
    
    # 连续阴线
    cc=0
    for i in range(len(klines_1h)-1,0,-1):
        o=float(klines_1h[i][1]); c=float(klines_1h[i][4])
        if c<o: cc+=1
        else: break
    kline_data["consecutive_red"]=cc
    
    # 4h K线
    if klines_4h: kline_data["klines_4h"]=klines_4h
    
    # 新指标
    kline_data.update(calc_bollinger_bands(klines_1h))
    kline_data.update(calc_ema_multi(klines_1h))
    kline_data.update(calc_macd(klines_1h))
    kline_data.update(calc_volume_quality(klines_1h))
    kline_data.update(calc_support_resistance(klines_1h))
    kline_data.update(calc_volume_pattern(klines_1h))
    kline_data.update(detect_consolidation_box(klines_1h))
    
    # 其他数据
    funding_data = {}
    if funding and len(funding)>0:
        funding_data["rate"]=float(funding[-1]["fundingRate"])
    
    oi_data = {}
    if oi_hist and len(oi_hist)>=2:
        s=float(oi_hist[0]["sumOpenInterest"]); e=float(oi_hist[-1]["sumOpenInterest"])
        if s>0: oi_data["oi_change_pct"]=(e-s)/s*100
    
    ls_data = {}
    if top_ls and len(top_ls)>0:
        ls_data["long_ratio"]=float(top_ls[-1]["longAccount"])*100
    
    taker_data = {}
    if taker and len(taker)>0:
        taker_data["volume_ratio"]=float(taker[-1].get("buySellRatio",1))
    
    # 生成报告
    report = generate_report(sym, kline_data, funding_data, oi_data, ls_data, taker_data)
    return report


def main():
    symbols = sys.argv[1:] if len(sys.argv) > 1 else ["BEATUSDT", "VELVETUSDT", "HAIOUSDT", "AIOUSDT"]
    
    print(f"🔍 复盘分析: {', '.join(symbols)}")
    print(f"{'='*50}")
    
    for sym in symbols:
        report = analyze_coin(sym)
        if report:
            print(report)
        time.sleep(0.5)  # 避免API限频
    
    print(f"\n{'='*50}")
    print(f"✅ 分析完成")


if __name__ == "__main__":
    main()
