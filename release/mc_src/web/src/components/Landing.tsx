interface Props {
  onEnter: (view: string) => void
}

// 2026-11-15 起：产品不收费、不设付费档、不需要注册。
// 这里保留三张卡片是为了说明「免费包含什么」，不是价格梯度。
const FREE = [
  {
    name: '个人使用', tag: '零配置 · 开箱即用', price: '¥0', unit: '永久免费',
    feat: ['ShellMate 命令行客户端', '本地静态安全门控', '本地优先 / 云端兜底',
      '5 个免费大模型自动切换', '无需注册、无需信用卡'],
    cta: '查看接入向导', key: 'guide', primary: false,
  },
  {
    name: '团队自用', tag: '全部功能 · 无阉割', price: '¥0', unit: '永久免费',
    feat: ['包含个人版全部能力', '团队控制台（用量/审计）', '安全策略下发与本地强制执行',
      '按客户端限流与模型停用', '审计哈希链校验与导出', 'Docker 单端口一键部署'],
    cta: '进入团队控制台', key: 'team', primary: true,
  },
  {
    name: '二次开发', tag: '源码开放 · 可自部署', price: '¥0', unit: '永久免费 · AGPL-3.0',
    feat: ['完整前后端源码', '可内网/离线私有部署', '可修改、可分发、可商用',
      '自带 Key，数据只在自己机器', '社区共建，欢迎 PR 与反馈'],
    cta: '查看文档与 API', key: 'guide', primary: false,
  },
]

export default function Landing({ onEnter }: Props) {
  return (
    <div className="view landing">
      <section className="hero">
        <div className="kicker">SafeShell Pro · 安全 AI 运维命令助手</div>
        <h1>把自然语言变成<em>安全</em>的运维命令<br />完全<em>免费</em>，源码开放</h1>
        <p className="lede">本地优先 + 安全门控，叠加免费大模型额度路由。危险命令硬拦截、推理成本为 0、全程可审计。不收费、不设付费档、不需要注册 —— 自己申请各厂商免费 API Key，数据留在自己机器。</p>
        <div className="cta-row">
          <button className="btn primary lg" onClick={() => { window.location.href = '/assistant'; }}>🚀 打开命令小助手</button>
          <button className="btn ghost lg" onClick={() => onEnter('team')}>进入团队控制台</button>
          <button className="btn ghost lg" onClick={() => onEnter('guide')}>查看接入向导</button>
        </div>
      </section>

      <section className="values">
        <div className="vc"><div className="vi">🛡️</div><b>安全门控</b><span>参数级规则引擎，高危命令（rm -rf / 格式化 / 提权）本地硬拦截，命令缓冲→校验→整行呈现。</span></div>
        <div className="vc"><div className="vi">🔮</div><b>执行后果模拟</b><span>命令小助手会先告诉你这条命令执行后会改哪些文件、能否撤销、会不会联网——纯文字推演，绝不真的执行。对小白友好。</span></div>
        <div className="vc"><div className="vi">💸</div><b>真的免费</b><span>软件本身 0 元、无付费档；默认走 5 个已验证免费大模型，实付恒为 0。报告里的「预计节省」只是相对付费等价模型的对照估算，不是账单、也不是收费依据。</span></div>
        <div className="vc"><div className="vi">🔍</div><b>全程可审计</b><span>每次调用的来源、模型、延迟落库，哈希链防篡改，可一键校验与导出。</span></div>
        <div className="vc"><div className="vi">🏠</div><b>本地优先</b><span>本机有大模型就本地跑（隐私不出机器）；无则自动回退云端，数据离开本机有提示。</span></div>
      </section>

      <section className="flow">
        <h2>它怎么工作</h2>
        <div className="flow-grid">
          <div className="fc"><span className="n">1</span><b>ShellMate（客户端）</b><span>自然语言 → 命令草稿，本地门控校验风险等级。</span></div>
          <div className="fc"><span className="n">2</span><b>网关（余量路由）</b><span>免费额度监控、耗尽自动切换、按客户端限流、审计落库。</span></div>
          <div className="fc"><span className="n">3</span><b>免费大模型</b><span>5 个已实测免费模型（GLM-Flash + 硅基流动 4 模型）；按任务路由、过载自动切换、政策变化一键停用。</span></div>
        </div>
      </section>

      <section className="pricing">
        <h2>完全免费 · 没有付费版</h2>
        <p className="center dim">所有功能一次给全，没有试用期限、没有席位费、没有「升级解锁」。唯一需要你自己准备的是各厂商的免费 API Key。</p>
        <div className="tiers">
          {FREE.map((t) => (
            <div key={t.name} className={`tier ${t.primary ? 'hot' : ''}`}>
              {t.primary && <div className="ribbon">全部功能</div>}
              <div className="t-name">{t.name}</div>
              <div className="t-tag">{t.tag}</div>
              <div className="t-price">{t.price}<small>{t.unit}</small></div>
              <ul className="t-feat">
                {t.feat.map((f) => <li key={f}>{f}</li>)}
              </ul>
              <button className={`btn ${t.primary ? 'primary' : 'ghost'} block`} onClick={() => onEnter(t.key)}>
                {t.cta}</button>
            </div>
          ))}
        </div>
        <div className="foot-note center">开源许可 AGPL-3.0 · 自行承担各厂商免费额度的合规使用 · 无官方托管服务、无 SLA 承诺</div>
      </section>
    </div>
  )
}
