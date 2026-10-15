"""Original equation notes derived from the inspected PLECS connections and KCL/KVL.

Reference parameters are model definitions, never estimates for an uploaded CSV.
The numerical identification implementation is unchanged by this corpus.
"""

SOURCES = {
    "local-plecs": {"title": "本地 PLECS 模型连接与参数证据", "path": "backend/knowledge_base/model_evidence.json", "url": None},
    "local-fitter": {"title": "本项目积分辨识与连续前向验证实现", "path": "backend/identification.py", "url": None},
    "plecs-demo": {"title": "Plexim: Buck Converter with Parameter Sweep", "url": "https://docs.plexim.com/plecs-demos/power-supplies/buck-converter-with-parameter-sweep/"},
    "plecs-opamp": {"title": "Plexim: finite-gain Op-Amp", "url": "https://docs.plexim.com/plecs/latest/components-by-category/opamp/"},
    "ti-buck": {"title": "TI SLVA057: Understanding Buck Power Stages", "url": "https://www.ti.com/lit/an/slva057/slva057.pdf"},
    "ti-sizing": {"title": "TI SLVA477: Basic Calculation of a Buck Converter's Power Stage", "url": "https://www.ti.com/lit/an/slva477b/slva477b.pdf"},
    "infineon-loss": {"title": "Infineon: Designing with power MOSFETs", "url": "https://www.infineon.com/assets/row/public/documents/24/42/infineon-designing-with-power-mosfets-applicationnotes-en.pdf"},
    "pe-gpt": {"title": "PE-GPT：文档分块、检索与工具协作参考", "url": "https://github.com/XinzeLee/PE-GPT"},
}


def entry(key, title, tags, scope, equations, explanation, source_ids):
    return {"id": key, "title": title, "tags": tags, "kind": "formula", "url": None,
            "scope": scope, "equations": equations.strip(), "explanation": explanation.strip(),
            "references": [SOURCES[s] for s in source_ids],
            "content": f"[{key}] {title}\n适用条件：{scope}\n公式：\n{equations.strip()}\n说明：{explanation.strip()}"}


FORMULA_DOCUMENTS = [
entry("buck-map", "Buck 电路与公式总览 / Circuit map",
      "总览 全部 所有 电路 功率 控制 公式 overview all equations circuit topology",
      "本项目知识范围是已检查的非同步 Buck 及其模拟电压控制网络，不覆盖任意 Buck 拓扑。",
      """功率级：Vin → T1 → 开关节点 → L1 → vo；D1 从地续流到开关节点。
vo 对地连接 C1、固定负载 R，以及经 S1 接入的 R5。
控制级：vo—R1—va—(R2 || C2)—vm；vm—R4—地；vm—C3—vb—R3—vop。
运放：正输入 Vref，负输入 vm，输出 vop；vop → Gain → Saturation → PWM → T1。
完整状态：x = [iL, vo, x2, x3]^T，x2=va-vm，x3=vb-vm。
轻量辨识状态：x_p = [iL, vo]^T，输入是实际测得的 PWM q(t)。""",
      """功率公式包括开关分段、CCM/DCM、稳态、纹波、应力、寄生、平均模型和小信号；控制公式包括有限增益运放 KCL、补偿网络、PWM、限幅、闭环及离散扩展；辨识公式包括积分回归与验证指标。
Case16A/B 使用上述闭环网络；独立的 Case17 固定占空比模型删除了反馈和切换负载。知识库中的模型值只能解释明确指定的参考模型，不能当作新 CSV 的辨识结果。""",
      ["local-plecs", "plecs-demo", "pe-gpt"]),
entry("buck-symbols", "符号、单位与电流方向 / Symbols and units",
      "符号 单位 定义 方向 symbols units definitions lcr esr",
      "下文采用 SI；q 表示瞬时门极，d 表示周期占空比，s 表示拉普拉斯变量。",
      """Vin, vo, vC, va, vb, vm, vop, Vref：V；iL, io, iC, isense：A。
L=L1：H；C=C1：F；Rload、R1…R5、rL、rC、Ron：Ω。
fs：Hz；Ts=1/fs：s；d=ton/Ts：无量纲；q∈{0,1}。
io=vo/Rload；iC=iL-io-isense；isense=(vo-va)/R1。
g1=1/R1，g3=1/R3，g4=1/R4：S；A：运放开环增益。
1 μH=10^-6 H；1 μF=10^-6 F；1 nF=10^-9 F；1 kHz=10^3 Hz。""",
      "Rload 是功率负载；rC 是电容 ESR；rL 是绕组电阻；R1～R4 是反馈补偿电阻。它们不能统称同一个 R。vo 为输出端电压，只有理想电容无 ESR 时才等于储能电容电压 vC。",
      ["local-plecs", "local-fitter"]),
entry("buck-reference-values", "PLECS 参考工况与初值 / Model definitions",
      "参数 初值 case16 case16a case16b case17 nominal initial conditions reference values",
      "这些是 .plecs 文件内部读取的设定值，未据此运行新的 PLECS 仿真，也不能用来猜上传数据的参数。",
      """原参数扫描模型：Vin=28 V，varL=40 μH，C1=500 μF，R=6 Ω，varR=1 Ω，fs=100 kHz。
Case16A：L=60 μH，C1=500 μF；Case16B：L=40 μH，C1=750 μF。
原模型与 Case16：R1=11 kΩ，R2=85 kΩ，R3=120 kΩ，R4=47 kΩ；C2=1.1 nF，C3=2.7 nF。
A=10^6，Vref=5 V，Gain=0.25 V^-1，dmin=0.1，dmax=0.9。
iL(0)=0 A，vo(0)=12 V，x2(0)=x3(0)=0 V；总时长 2 ms，负载切换 tstep=1 ms。
独立 Case17 基准：Vin=28 V，L=50 μH，C1=1000 μF，Rload=3 Ω，fs=20 kHz，d=0.22，iL(0)=vo(0)=0，总时长 10 ms。""",
      "原文件中的初始化命令仅对应普通运行；参数扫描脚本可能覆盖 varL/varR。若重新导出的模型与证据快照不同，需要重新检查。Case17 与 Case16 不能混用控制电路、频率、初值或负载设定。",
      ["local-plecs"]),
entry("buck-switch-states", "功率级开关分段微分方程 / Switching states",
      "导通 关断 二极管 续流 开关 微分 状态 功率 方程 on off diode switching ode power",
      "理想非同步 Buck，Vin>vo≥0；iL 方向由开关节点流向输出。控制支路存在时保留 isense。",
      """T1 导通 q=1：vL=Vin-vo，diL/dt=(Vin-vo)/L。
T1 关断且 D1 续流 q=0,iL>0：vL=-vo，diL/dt=-vo/L。
两种有电感电流的区间：C dvo/dt = iL-vo/Rload-isense。
关断且 iL=0 的 DCM 区间：diL/dt=0，C dvo/dt=-vo/Rload-isense。
无反馈取电时，零电流区 vo(t)=vo(t0) exp[-(t-t0)/(Rload C)]。""",
      "不能在二极管已经截止时继续用 diL/dt=-vo/L 得到负电流。同步 Buck 的负电流、反向功率和 Vin<vo 等工况不属于该单向简化模型。网页拟合的两状态模型取 isense=0，使用实际 PWM 而非控制器重建。",
      ["local-plecs", "local-fitter", "ti-buck"]),
entry("buck-load-step", "负载跳变与反馈取电 / Load switching",
      "负载 跳变 突变 并联 r5 s1 1ms load step parallel sensing",
      "原参数扫描模型和 Case16A/B；R5=varR>0，S1 由 Step 控制。",
      """u_load(t)=0 (t<1 ms)，u_load(t)=1 (t≥1 ms)。
Gload(t)=1/R + u_load(t)/R5，Rload(t)=1/Gload(t)。
R=6 Ω、R5=1 Ω 时：Rbefore=6 Ω，Rafter=6/7 Ω≈0.857143 Ω。
io(t)=Gload(t) vo(t)，isense=(vo-va)/R1。
在理想 C1 上，vo 连续；电感电流 iL 连续。
Δ(dvo/dt)|step = -vo(tstep) ΔGload/C，条件是其他状态与 isense 在该时刻连续。""",
      "负载突变不能把全段当作单一恒定 R 辨识。应分别选择切换前后区间；当前网页默认示例 0～0.8 ms。若扫描脚本令 varR=0，属于理想短路情形，不能代入 1/R5，需按电路约束单独处理。",
      ["local-plecs"]),
entry("buck-ccm-steady", "CCM 稳态与变换比 / Steady-state conversion",
      "稳态 占空比 变换比 平衡 输出 平均 ccm duty steady conversion balance",
      "周期稳态、连续导通、理想功率器件、输出纹波相对小。",
      """∫_0^Ts vL dt=0；∫_0^Ts iC dt=0。
d(Vin-Vo)+(1-d)(-Vo)=0 ⇒ Vo=d Vin。
IL=Vo/Rload+Isense；无取电近似 IL=Io=Vo/Rload。
Iin≈d IL，Pin=Vin Iin，Pout=Vo^2/Rload。
理想且不计反馈耗能：Pin=Pout，η=Pout/Pin=1。""",
      "Vo=dVin 不适用于未稳态启动波形或 DCM。闭环 d 随输出误差变化；不能用初始 12 V 当稳态目标。反馈取电和控制器辅助电源需明确功率边界，不能无条件把总效率设为 100%。",
      ["local-plecs", "ti-buck"]),
entry("buck-ripple-sizing", "电感、电容纹波与选值 / Ripple and sizing",
      "纹波 选型 选值 电感 电容 容量 ripple sizing delta inductance capacitance",
      "理想 CCM 周期稳态、近似三角电感电流，忽略控制支路的高频负载变化。",
      """ΔiL,pp=(Vin-Vo)d/(L fs)=Vo(1-d)/(L fs)。
IL,pk=Io+ΔiL,pp/2；IL,min=Io-ΔiL,pp/2。
L≥(Vin-Vo)d/(fs ΔiL,allowed)，需对输入范围逐点检查最大纹波。
Δvo,C,pp≈ΔiL,pp/(8 fs C)；Δvo,ESR,pp≈rC ΔiL,pp。
保守纹波预算：Δvo,pp≲ΔiL,pp/(8 fs C)+rC ΔiL,pp。
C≥ΔiL,pp/[8 fs(Δvo,allowed-rC ΔiL,pp)]，分母必须>0。
负载阶跃初期 Δvo≈-ΔIo Δt/C；ESR 引起额外瞬时端电压变化约 -rC ΔIo。""",
      "电容和 ESR 纹波一般不同时达到峰值，直接相加是预算近似，不是精确波形等式。还需容差、直流偏置、温度、饱和及纹波额定值；阶跃近似仅适用于控制器尚未补偿、iL 近似不变的短时间。",
      ["ti-sizing", "local-plecs"]),
entry("buck-dcm", "DCM 变换比与 CCM 边界 / Discontinuous conduction",
      "dcm ccm bcm 断续 连续 临界 边界 轻载 conduction boundary critical",
      "理想非同步 Buck、固定 d、周期稳态、输出近似恒压、无反馈支路负载。",
      """临界 Io,crit=ΔiL,pp/2；Lcrit=Rload(1-d)/(2 fs)。
CCM：L>Lcrit；边界 L=Lcrit；DCM：L<Lcrit（给定稳态 d、Rload）。
定义 M=Vo/Vin，K=2 L fs/Rload，δ 为二极管续流占空比。
Ipk=(Vin-Vo)d/(L fs)，δ=d(1-M)/M。
Io=Ipk(d+δ)/2；K M^2=d^2(1-M)。
M=2/[1+sqrt(1+4K/d^2)]，且 d+δ=d/M<1。
第三段比例=1-d-δ；边界 K=1-d，M=d。""",
      "不能在 DCM 直接使用 M=d；边界公式含实际负载，不是仅凭电感大小判断。反馈支路或恒功率负载存在时应使用其电流模型重新推导。",
      ["ti-buck"]),
entry("buck-rms-stress", "电流 RMS、器件应力与储能 / Electrical stress",
      "rms 应力 峰值 有效值 能量 输入电容 mosfet diode stress energy",
      "理想 CCM，iL 在 Io±Δi/2 之间线性变化，忽略 isense。",
      """IL,rms=sqrt(Io^2+Δi^2/12)，ICout,rms=Δi/sqrt(12)。
IT1,avg=d Io；IT1,rms=sqrt[d(Io^2+Δi^2/12)]。
ID1,avg=(1-d)Io；ID1,rms=sqrt[(1-d)(Io^2+Δi^2/12)]。
Icin,rms≈sqrt[d(1-d)Io^2+d Δi^2/12]（上游只供平均输入电流）。
开关关断电压、二极管反压理想值约 Vin；峰值电流约 Io+Δi/2。
EL=L iL^2/2；EC=C vC^2/2。""",
      "实际电压尖峰还取决于寄生电感、布局、结电容和反向恢复。额定值要覆盖瞬态；RMS 不能简单用平均值代替。这里只给电应力，不能由此直接算剩余寿命。",
      ["ti-sizing", "infineon-loss"]),
entry("buck-nonideal", "ESR、绕组电阻与器件压降 / Nonideal power stage",
      "esr 寄生 绕组 电阻 压降 非理想 ron vf parasitic nonideal winding",
      "扩展模型：输出电容串联 rC，电感串联 rL，MOSFET Ron，二极管 Vf+rD iL。它们未加入当前网页辨识器。",
      """vo=vC+rC iC，C dvC/dt=iC，iC=iL-vo/Rload-isense。
若 isense=0：vo=(vC+rC iL)/(1+rC/Rload)。
导通：L diL/dt=Vin-vo-(rL+Ron)iL。
续流：L diL/dt=-vo-Vf-(rL+rD)iL。
CCM 小纹波稳态：Vo≈d Vin-(1-d)Vf-Io[rL+d Ron+(1-d)rD]。
理想与非理想参数不可混同：Rload≠rL≠rC。""",
      "有反馈取电时需联立输出端 KCL 求 vo，而非直接套用 isense=0 的代数式。原 PLECS T1 Ron=0、D1 Vf=Ron=0，未设置独立 ESR/绕组电阻；默认理想元件也没有可计算的真实损耗参数。",
      ["local-plecs", "ti-buck"]),
entry("buck-loss-thermal", "损耗、温升与寿命边界 / Loss and thermal estimates",
      "损耗 效率 温升 热 mosfet 栅极 铜损 铁损 loss thermal efficiency lifetime",
      "硬件扩展知识，需要真实器件和热边界；不是原理想 PLECS 仿真的已有输出。",
      """Pcond,T1=Ron(Tj) IT1,rms^2；Pdiode≈Vf ID,avg+rD ID,rms^2。
Pcu≈rL(T) IL,rms^2；Pcap≈ESR(f,T) IC,rms^2。
Psw=fs(Eon+Eoff)；粗略重叠估计 Psw≈0.5 Vin Isw(tr+tf) fs。
Pgate≈Qg Vdrive fs（主要属于驱动供电损耗，不全是 MOSFET 芯片热损耗）。
Ptotal=各不重复损耗之和；η=Pout/(Pout+Ptotal)。
稳态 Tj≈Ta+Pdevice RθJA，或 Tj≈Tc+Pdevice RθJC；单阶跃 ΔTj(t)=Pstep Zθ(t)。
若特定材料支持 Steinmetz 模型：Pcore=k f^α Bpk^β Vcore；参数和波形适用域必须来自该磁材。
仅在厂商允许时 Llife=Lref·2^[(Tref-Thot)/10]；不具备通用剩余寿命含义。""",
      "开关能量应按实际电流、电压、栅阻和温度校正，避免把已包含的 Coss/恢复能量重复计入。RθJA 与安装条件有关，不能与 RθJC 混用。非正弦磁通需要相应损耗模型。电容寿命还需要系列、热点温度、额定寿命及历史，不能由 C 漂移或短时波形直接推断年数。",
      ["infineon-loss"]),
entry("buck-average", "功率级平均状态空间 / Averaged state space",
      "平均 状态空间 矩阵 averaged state space matrix",
      "CCM、平均频率远低于 fs、恒定 Rload、理想功率级，暂忽略反馈取电。",
      """x=[iL,vo]^T；dx/dt=Ap x+Bp d Vin。
Ap=[[0,-1/L],[1/C,-1/(Rload C)]]，Bp=[1/L,0]^T。
稳态 x0=[D Vin/Rload,D Vin]^T。
小信号：d(x_hat)/dt=Ap x_hat+[Vin/L,0]^T d_hat+[D/L,0]^T vin_hat-[0,1/C]^T idist_hat。""",
      "idist 是额外从输出抽走的扰动电流，区别于已在 Ap 中的电阻负载电流。DCM、限幅、大信号启动、切换频率附近不能直接使用此线性模型。",
      ["local-plecs", "ti-buck"]),
entry("buck-transfer", "功率级传递函数、极点与阻尼 / Plant transfer functions",
      "传递函数 小信号 极点 阻尼 谐振 bode transfer poles damping resonance gvd",
      "理想 CCM、小信号、固定负载、忽略取电，零初始扰动。",
      """Den(s)=L C s^2+(L/Rload)s+1。
Gvd(s)=vo_hat/d_hat=Vin/Den(s)；Gvg(s)=vo_hat/vin_hat=D/Den(s)。
Zout(s)=-vo_hat/idist_hat=L s/Den(s)。
ω0=1/sqrt(L C)，f0=ω0/(2π)，Q=Rload sqrt(C/L)，ζ=1/(2Q)。
p1,2=-1/(2 Rload C) ± sqrt[(1/(2 Rload C))^2-1/(L C)]。
仅加入电容 ESR rC 时：Gvd(s)=Vin(1+s rC C)/[1+s(L/Rload+rC C)+s^2 L C(1+rC/Rload)]。
ESR 零点 ωz,ESR=1/(rC C)。""",
      "上述 ESR 公式未包含 rL/Ron/Vf/控制支路。完整闭环需要联立四状态或其线性化；不能仅由这两个功率级极点判定闭环稳定。",
      ["local-plecs", "ti-buck"]),
entry("buck-controller-kcl", "实际 R1–R4、C2/C3 控制电路 KCL / Analog network",
      "控制电路 运放 补偿 r1 r2 r3 r4 c2 c3 节点 kcl analog opamp controller",
      "按本地 .plecs 的连接关系推导；理想无限输入阻抗、有限开环增益 A，无运放带宽/压摆率/电源轨限幅模型。",
      """定义：x2=va-vm，x3=vb-vm，vop=A(Vref-vm)。
节点 va： (vo-va)/R1 = x2/R2 + C2 dx2/dt。
节点 vb： (vop-vb)/R3 = C3 dx3/dt。
节点 vm： x2/R2+C2 dx2/dt+C3 dx3/dt=vm/R4。
令 Δ=g1+g4+(A+1)g3：
vm=[g1(vo-x2)+g3(A Vref-x3)]/Δ。
va=vm+x2，vb=vm+x3；isense=g1(vo-vm-x2)。
dx2/dt=[isense-x2/R2]/C2。
dx3/dt=[vop-vm-x3]/(R3 C3)。""",
      "这不是随意设置 Kp/Ki/Kd 的数字 PID。R2 与 C2 并联；R3 与 C3 串联反馈；R4 接反相节点到地。有限 A 必须保留，若直接强制 vm=Vref，会失去实际有限增益模型的信息。",
      ["local-plecs", "plecs-opamp"]),
entry("buck-controller-state", "完整闭环四状态方程与数值初值 / Closed-loop states",
      "闭环 四状态 初始状态 完整 状态 初值 autonomous closed loop initial state finite gain",
      "原模型/Case16 的理想元件网络；输入 Vin、Vref，负载开关 u_load。q 来自控制器，不来自测量。",
      """x=[iL,vo,x2,x3]^T。
vm=[g1(vo-x2)+g3(A Vref-x3)]/[g1+g4+(A+1)g3]。
vop=A/[g1+g4+(A+1)g3]·[Vref(g1+g4+g3)-g1(vo-x2)+g3 x3]。
isense=g1(vo-vm-x2)。
diL/dt=(q Vin-vo)/L（有电感电流区间；DCM 按分段约束）。
dvo/dt=[iL-Gload vo-isense]/C。
dx2/dt=[isense-x2/R2]/C2；dx3/dt=[vop-vm-x3]/(R3 C3)。
x(0)=[0 A,12 V,0 V,0 V]^T（原模型和 Case16 文件设定）。""",
      "vop 的第二个表达式与 A(Vref-vm) 代数等价，可减少大 A 下的消减误差。截取 CSV 中间时间窗时，x2/x3 一般不再为零，不能沿用全仿真初值。当前网页尚未用该四状态模型辨识或自主闭环验证；知识层提供公式解释，不自动改变计算器。",
      ["local-plecs", "plecs-opamp", "local-fitter"]),
entry("buck-controller-transfer", "实际模拟补偿器传递函数 / Compensator transfer",
      "补偿器 传递函数 pid 模拟 阻抗 zi zf gc compensator transfer analog filtered",
      "零初始小信号；Zi 从 vo 接到 vm，Zf 从 vop 接到 vm。A 为有限常数。",
      """Zi(s)=R1+R2/(1+s R2 C2)；Zf(s)=R3+1/(s C3)。
Yi=1/Zi，Yf=1/Zf；B(s)=Yi+g4+(A+1)Yf。
vop_hat=Gr,A(s) vref_hat-Gc,A(s) vo_hat。
Gc,A=A Yi/B；Gr,A=A(Yi+g4+Yf)/B。
A→∞：Gc=Zf/Zi=(1+s R3 C3)(1+s R2 C2)/{s C3(R1+R2)[1+s(R1||R2)C2]}。
A→∞：Gr=1+Gc+Zf/R4。
固定参考时 vop_hat=-Gc vo_hat；不能误写为正反馈。""",
      "理想补偿器可视为带高频滚降的模拟 PID 网络，但有限 A 下必须使用 Gc,A。参考通道 Gr 与输出反馈通道 Gc 不同，不能简单写成同一个 Gc(Vref-vo)。不要未经拓扑检查套用通用 Type-III 模板。",
      ["local-plecs", "plecs-opamp"]),
entry("buck-controller-poles", "补偿器零极点与等效 PID / Controller poles and zeros",
      "零点 极点 pid kp ki kd 微分 积分 补偿 bode zeros poles gains",
      "A→∞ 的 Zi/Zf 网络近似；仅用于理解该模拟补偿器的频率特征。",
      """τz1=R3 C3，τz2=R2 C2，τp=(R1||R2)C2，K=1/[C3(R1+R2)]。
Gc(s)=K(1+sτz1)(1+sτz2)/[s(1+sτp)]。
零点：s=-1/τz1、-1/τz2；极点：s=0、-1/τp。
本地元件值：fz1≈491.219 Hz，fz2≈1702.192 Hz，fp≈14855.493 Hz，K≈3858.025 s^-1。
等效形式 Gc=Ki/s+Kp+Kd s/(1+sτp)：
Ki=K；Kp=K(τz1+τz2-τp)；Kd=K(τz1τz2-τp(τz1+τz2-τp))。""",
      "等效系数由该 RC 网络代数分解得到，不代表 PLECS 存在独立 PID 控制块。该理想传函是两个零点、原点和一个有限极点，不应直接称作任意三极点 Type-III 网络。实际有限增益消除了理想积分器的无限直流增益，应回到 Gc,A 检查极低频。",
      ["local-plecs"]),
entry("buck-pwm", "增益、占空比限幅与 PWM / Modulation",
      "pwm 比较器 锯齿 占空比 限幅 gain saturation ramp comparator duty",
      "原模型/Case16：0～1 上升锯齿，fs=100 kHz；实际 Gate 仍应通过 Scope 导出。",
      """u=0.25 vop；d_cmd=min(0.9,max(0.1,u))。
r(t)=frac(fs t)，0≤r<1；q(t)=1 当 r(t)<d_cmd(t)，否则 q=0。
若每周期 d_cmd 近似恒定：ton≈d_cmd Ts，d_actual=(1/Ts)∫q(t)dt≈d_cmd。
未饱和小信号：d_hat=0.25 vop_hat；饱和区局部 dd_cmd/dvop=0。
更一般幅度 [Vr,min,Vr,max]：d≈(vc-Vr,min)/(Vr,max-Vr,min)，在允许区间截断。""",
      "连续模拟比较器中 d_cmd 可在周期内变化，此时 q 的实际脉宽不一定等于瞬时 d_cmd Ts。Operator=3 的比较方向结合连接与已核对的控制实现为 ramp<duty；相等时按比较器事件约定。限幅器限制 duty，不表示运放内部有供电轨限幅或抗积分饱和。",
      ["local-plecs"]),
entry("buck-control-dc", "闭环输出目标与直流误差 / DC setpoint",
      "目标 参考 5v 12v 15v 稳态 r4 直流 setpoint reference dc regulation",
      "CCM 稳态、反馈电容无直流电流、未进入 duty 限幅。原网络而非简单双电阻反馈分压。",
      """H0=R4/(R1+R2+R4)；vm=H0 Vo；isense=Vo/(R1+R2+R4)。
A→∞：Vo*=Vref/H0=Vref[1+(R1+R2)/R4]。
代入 Vref=5 V、R1+R2=96 kΩ、R4=47 kΩ：Vo*≈15.212766 V。
理想功率级加有限 A：Vo=(0.25 Vin A Vref)/(1+0.25 Vin A H0)。
稳态占空比 D=Vo/Vin；参考点需满足 0.1<D<0.9。""",
      "12 V 是 C1 初值，不是反馈目标；5 V 是运放正输入参考，也不是输出目标。约 15.21 V 是指定电路的理论设定值，不是当前 CSV 的测量值或辨识真值。实际损耗、DCM、限幅或元件变化时应重新求平衡。",
      ["local-plecs", "plecs-opamp"]),
entry("buck-loop", "闭环传函、反馈负载及稳定性 / Loop gain",
      "闭环 稳定 相位 裕度 交越 环路 传递函数 loop gain stability margin crossover bandwidth",
      "CCM、恒定负载、局部线性、PWM 未饱和；先给忽略取电的常用近似，再给包含控制取电的联立式。",
      """Km=0.25；T(s)=Km Gvd(s) Gc,A(s)。
忽略取电：vo_hat/vref_hat=Km Gvd Gr,A/(1+T)，vo_hat/vin_hat=Gvg/(1+T)。
vo_hat/idist_hat=-Zout/(1+T)。
包含取电：isense_hat=Yload,ctrl vo_hat+Yref,ctrl vref_hat。
Yload,ctrl=Yi(1-Yi/B)，Yref,ctrl=-Yi A Yf/B；B=Yi+g4+(A+1)Yf。
Dcl=1+Km Gvd Gc,A+Zout Yload,ctrl。
vo_hat/vref_hat=(Km Gvd Gr,A-Zout Yref,ctrl)/Dcl。
vo_hat/vin_hat=Gvg/Dcl；vo_hat/idist_hat=-Zout/Dcl。
|T(jωc)|=1 时 PM=180°+arg T(jωc)；arg T(jωπ)=-180° 时 GMdB=-20log10|T(jωπ)|。""",
      "包含取电时可以先把负载修正后的功率级写为 Gvd/(1+Zout Yload,ctrl)，再求环路裕度。频率须低于开关频率且远离未建模采样/延迟；有多个交越、右半平面极点、限幅或 DCM 时仅一个 PM 数字不足以证明稳定。没有扫频或计算工具结果时，不编造具体裕度。",
      ["local-plecs", "ti-buck"]),
entry("buck-discrete-control", "离散控制扩展与延迟 / Optional digital implementation",
      "离散 数字 pi pid 采样 延迟 积分饱和 digital discrete sampling antiwindup",
      "扩展设计知识；本地 PLECS 控制器是连续模拟 RC 网络，当前网页未实现数字控制器设计。",
      """数字 PI 示例：e[k]=vref[k]-H vo[k]。
ξ[k+1]=ξ[k]+Tctrl e[k]；u_raw[k]=Kp e[k]+Ki ξ[k]；d[k]=clip(u_raw[k],dmin,dmax)。
反算抗饱和示例：ξ[k+1]=ξ[k]+Tctrl{e[k]+Kaw[d[k]-u_raw[k]]}。
后向差分 PID 增量式：u[k]=u[k-1]+Kp(e[k]-e[k-1])+Ki Tctrl e[k]+Kd/Tctrl·(e[k]-2e[k-1]+e[k-2])。
零阶保持离散化：Ad=exp(Ap Tctrl)，Bd=∫_0^Tctrl exp(Ap τ) Bp dτ。
纯延迟：Gdelay(s)=exp(-s Td)，在 ω 下贡献相位 -ω Td rad。""",
      "这些公式是选定实现的例子，不是原模型的事实。Kaw 的单位随积分状态定义改变；微分项需滤波、采样周期需独立于仿真步长。不可把数字 PI 的 Kp/Ki 直接塞进原 R/C 元件值而不做映射。",
      ["local-plecs"]),
entry("buck-identification-formulas", "L/C/R 积分辨识回归 / Integral identification",
      "辨识 识别 积分 回归 最小二乘 lcr parameter identification integral regression least squares",
      "当前网页模型：已知 Vin、实际 PWM、理想非同步 Buck、区间内 Rload 恒定，忽略反馈取电。",
      """对窗口 [ta,tb]：L ΔiL=∫(q Vin-vo)dt，记 yL=∫(q Vin-vo)dt。
最小二乘：min_{L>0} Σw[yL,w-L ΔiL,w]^2。
电容 KCL 积分：∫iL dt=C Δvo+G∫vo dt，G=1/Rload。
Xw=[Δvo,w, ∫vo dt]；θ=[C,G]^T；min_{C>0,G>0} ||Xθ-y||²，Rload=1/G。
固定 R 时：C Δvo=∫iL dt-(1/R)∫vo dt。
完整反馈修正：把回归右侧 ∫iL dt 改为 ∫(iL-isense)dt。
变量负载：∫iL dt=C Δvo+∫Gload(t)vo dt+∫isense dt，不能直接当作单一常数 G 回归。""",
      "含关断零电流段的窗口不满足电感积分方程，要排除。C/G 两列需线性独立且有动态激励；仅稳态 DC 无法识别 C。网页实际做有界和列缩放回归，不是对每个噪声点做导数相除。增加知识条目不会把反馈修正自动接入现有算法。",
      ["local-fitter", "local-plecs"]),
entry("buck-identifiability", "可辨识性、噪声与参数误差 / Identifiability",
      "可辨识 条件数 噪声 误差 原因 修改 计划 rank noise identifiability bias error",
      "区分数据不足、模型遗漏和算法误差；不把波形拟合成功当作参数唯一或准确的证明。",
      """联合 C/G 需要 rank(X)=2；归一化列后的 κ2(X)=σmax/σmin 衡量相关性。
简化窗口方程：L=yL/ΔiL，C=(∫iL dt-G∫vo dt)/Δvo，仅在分母不接近零时有意义。
遗漏 isense 时，稳态 Gfit≈Gload+Isense/Vo，因而 Rfit 可能略小于真实负载。
若有独立真值 θtrue：relative_error=|θhat-θtrue|/|θtrue|×100%。
PWM 时移 δt 导致开关电压积分误差量级约 Vin·δt（单边沿）。""",
      "先查单位、PWM 对齐、Vin、负载分段，再查初值、取电支路、ESR/绕组压降和采样。积分可减少数值微分放大，但电压/电流同时带噪声时普通最小二乘仍会有自变量噪声偏差；必要时做多工况、工具变量或总最小二乘验证。",
      ["local-fitter", "local-plecs"]),
entry("buck-validation", "波形统计、验证与误差指标 / Validation metrics",
      "rmse nrmse 验证 时间加权 统计 平均 误差 validation metrics waveform mean",
      "非均匀时间戳、连续时间统计；当前实现对时间积分采用梯形规则，PWM 电压积分采用零阶保持。",
      """T=tend-tstart；mean(x)=(1/T)∫x(t)dt；RMS(x)=sqrt[(1/T)∫x(t)^2dt]。
RMSE=sqrt[(1/T)∫(xhat-x)^2dt]；NRMSE%=100 RMSE/max(RMS(x),10^-9)。
梯形规则：∫x dt≈Σ(x[k]+x[k+1])/2·(t[k+1]-t[k])。
PWM 零阶保持：∫q Vin dt≈Σ q[k] Vin·(t[k+1]-t[k])。
训练分界 t_split=t_start+f_train(t_end-t_start)。
连续验证：只在所选区间起点初始化，预测贯穿训练和后段，验证段不重置为测量状态。""",
      "RMS/NRMSE 归一化定义需说明，不要与按峰峰值归一化混用。当前网页使用测量 PWM、同一记录后段验证；这不是自主闭环，也不是独立工况外推。没有真实参数就不报告参数真实误差；新四状态知识不改变该验证协议。",
      ["local-fitter"]),
]
