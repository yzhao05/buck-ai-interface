"""Offline training-only voltage filters and comparator-output screening.

MS n=2, alpha=4 follows Schmid et al., ACS Meas. Sci. Au (2022),
doi:10.1021/acsmeasuresciau.1c00054, Eqs 3-6 (no n>=6 correction terms).
Boundary linear extension is an explicit project choice, not a causal filter.
"""
import numpy as np
from scipy.signal import savgol_filter, freqz


class PWMQualityError(ValueError):
    def __init__(self, diagnostics):
        self.diagnostics = diagnostics
        q = diagnostics['quality']
        super().__init__(f"PWM 重建质量检查未通过：{q['complete_cycles']} 个完整周期，上升沿 {q['rising_edges']}，下降沿 {q['falling_edges']}，额外边沿 {q['extra_edges']}，异常短脉冲 {q['short_pulses']}，缺少边沿周期 {q['missing_edge_cycles']}，占空比异常周期 {q['duty_violations']}。已停止参数辨识；请核对去噪窗口、采样和控制电路。")


def modified_sinc_kernel(length):
    m = (length-1)//2
    x = np.arange(-m,m+1,dtype=float)/(m+1)
    window = np.exp(-4*x*x)+np.exp(-4*(x-2)**2)+np.exp(-4*(x+2)**2)-2*np.exp(-4)-np.exp(-36)
    kernel = np.sinc(3*x)*window
    return kernel/kernel.sum()


def filter_voltage(voltage, method='sg', window_cycles=6.):
    # The RC solver uses 500 points per carrier period. No future holdout
    # samples are available to this routine, including its boundary fits.
    if method not in ('none','sg','modified_sinc') or not np.isfinite(window_cycles) or not .1 <= window_cycles <= 8:
        raise ValueError('无效的 PWM 去噪方法或窗口。')
    length = 2*int(round(window_cycles*500/2))+1
    if len(voltage)<length and method!='none':
        raise ValueError('训练段短于 PWM 去噪窗口，请缩短窗口。')
    if method=='none':
        filtered=voltage.copy();length=1;kernel=np.ones(1)
    elif method=='sg':
        from scipy.signal import savgol_coeffs
        filtered=savgol_filter(voltage,length,polyorder=2,mode='interp')
        kernel=savgol_coeffs(length,2)
    else:
        kernel=modified_sinc_kernel(length);m=length//2
        count=max(3,min(m,len(voltage)//2))
        x=np.arange(count,dtype=float)
        left=np.polyval(np.polyfit(x,voltage[:count],1),np.arange(-m,0))
        right=np.polyval(np.polyfit(x,voltage[-count:],1),np.arange(count,count+m))
        filtered=np.convolve(np.r_[left,voltage,right],kernel,mode='valid')
    omega,gain=freqz(kernel,worN=16384)
    cutoff=np.flatnonzero(np.abs(gain)<=1/np.sqrt(2))
    return filtered, {'method':method,'window_cycles':window_cycles if method!='none' else 0,
        'window_samples':length,'sg_degree':2 if method=='sg' else None,'ms_degree':2 if method=='modified_sinc' else None,
        'cutoff_over_switch_frequency':float(omega[cutoff[0]]/(2*np.pi)*500) if len(cutoff) else None,
        'boundary':'polynomial_interp' if method=='sg' else 'linear_extension' if method=='modified_sinc' else 'none',
        'offline_noncausal':True,'uses_heldout_measurements':False,'fit_measurements_smoothed':False,
        'change_rms_V':float(np.sqrt(np.mean((filtered-voltage)**2)))}


def pwm_quality(gate, profile):
    """Count real comparator transitions; never repair or latch the output.

    Gate[k] covers [k*dt,(k+1)*dt). Incomplete cycles still expose extra
    transitions, but cannot establish missing edges or full-cycle duty.
    Exclude censored first/last runs from pulse widths.
    These thresholds screen plausibility, not reconstruction accuracy.
    """
    dt=profile['dt_s'];phase=profile['carrier_phase_at_t0']
    gate=np.asarray(gate,dtype=float)
    changes=np.diff(gate)
    rises=np.flatnonzero(changes>0)+1;falls=np.flatnonzero(changes<0)+1
    ids=np.floor(np.arange(len(gate)-1)/500+phase+1e-12).astype(int)
    edges=np.flatnonzero(changes!=0)+1
    intervals=np.diff(np.r_[0,edges,len(gate)-1])[1:-1]
    short_threshold=max(2*dt,.5*min(profile['duty_min'],1-profile['duty_max'])/profile['frequency_hz'])
    short=int(np.count_nonzero(intervals*dt<short_threshold-1e-14))
    cycles=[];partial_cycles=[];extra=0;missing=0;bad_duty=0
    for cycle in np.unique(ids):
        indices=np.flatnonzero(ids==cycle)
        lo,hi=int(indices[0]),int(indices[-1]+1)
        nr=int(np.sum((rises>=lo)&(rises<hi)));nf=int(np.sum((falls>=lo)&(falls<hi)))
        # At t=0 a high initial gate is a censored rising edge, not missing.
        if lo==0 and gate[0]==1:nr+=1
        extra+=max(0,nr-1)+max(0,nf-1)
        if len(indices)!=500:
            partial_cycles.append({'cycle':int(cycle),'samples':len(indices),'rises':nr,'falls':nf})
            continue
        duty=float(np.mean(gate[lo:hi]))
        constant_allowed = (duty==0 and profile['duty_min']==0) or (duty==1 and profile['duty_max']==1)
        missing+=int((nr==0 or nf==0) and not constant_allowed)
        bad_duty+=int(duty<profile['duty_min']-.004 or duty>profile['duty_max']+.004)
        cycles.append({'cycle':int(cycle),'duty':duty,'rises':nr,'falls':nf})
    # A few genuine crossings can occur during rapid transients; explicitly
    # reject ANY unexpected multi-crossing cycle instead of silently locking it.
    passed=bool(cycles) and extra==0 and short==0 and bad_duty==0 and missing==0
    return {'version':'pwm-plausibility-v2','status':'passed' if passed else 'rejected',
        'complete_cycles':len(cycles),'rising_edges':len(rises),'falling_edges':len(falls),
        'extra_edges':extra,'short_pulses':short,'missing_edge_cycles':missing,'duty_violations':bad_duty,
        'minimum_uncensored_pulse_s':float(min(intervals)*dt) if len(intervals) else None,
        'short_pulse_threshold_s':short_threshold,'duty_tolerance':.004,'cycles':cycles,'partial_cycles':partial_cycles,
        'interpretation':'Heuristic plausibility gate, not PWM accuracy validation. No artificial latching or edge repair.'}
