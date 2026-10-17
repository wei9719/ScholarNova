import { describe, expect, it } from 'vitest'
import { buildRouteDocumentHtml, cleanRouteMarkdown } from '../routeDocument'

const origin = 'http://127.0.0.1:5173'
const route = { title: '研究路线', status: 'active', description: '', ai_analysis: '' }
const parse = (html: string) => new DOMParser().parseFromString(html, 'text/html')

describe('research route document export', () => {
  it('renders untrusted titles, status, text and model labels as text, not active HTML', () => {
    const title = '</title><script>window.pwned = true</script><h1>Injected</h1>'
    const malicious = '<img src=x onerror="window.pwned=true"> & <svg onload="window.pwned=true">'
    const html = buildRouteDocumentHtml({
      title,
      status: malicious,
      description: malicious,
      ai_analysis: `## 文字分析（${malicious}）\n${malicious}\n## 研究架构图（${malicious}）\n${malicious}`,
    }, origin)
    const doc = parse(html)
    expect(doc.title).toBe(title)
    expect(doc.querySelector('h1')?.textContent).toBe(title)
    expect(doc.querySelectorAll('h1')).toHaveLength(1)
    expect(doc.querySelectorAll('script, img, svg, iframe, object')).toHaveLength(0)
    expect(doc.body.textContent).toContain(malicious)
    expect([...doc.querySelectorAll('span')].every((span) => span.textContent?.includes(malicious))).toBe(true)
  })

  it('blocks non-web image schemes and prevents image-attribute injection', () => {
    const doc = parse(buildRouteDocumentHtml({
      ...route,
      ai_analysis: [
        '![bad](javascript:alert(1))',
        '![bad](data:image/svg+xml;base64,AAAA)',
        '![bad](file:///etc/passwd)',
        '![bad](//other.example/image.png)',
        '![bad](/\\other.example/image.png)',
        '![quote](https://images.example/figure.png" onerror="window.pwned=true)',
      ].join('\n'),
    }, origin))
    const images = doc.querySelectorAll('img')
    expect(images).toHaveLength(1)
    expect(images[0].getAttribute('src')).toContain('https://images.example/figure.png%22')
    expect(images[0].hasAttribute('onerror')).toBe(false)
    expect([...doc.querySelectorAll('*')].flatMap((el) => [...el.attributes])
      .some((attr) => attr.name.startsWith('on'))).toBe(false)
  })

  it('preserves paragraphs, model names and local / HTTP(S) figure URLs', () => {
    const doc = parse(buildRouteDocumentHtml({
      ...route,
      title: 'A & B <研究>',
      description: '**目标**\n第二阶段',
      ai_analysis: '## 文字分析（zhipu/glm-5.2）\n比较 A & B\n## 研究架构图（sensenova/u1）\n说明\n![本地图](/generated/route.png)\n![远程图](https://images.example/route.png?a=1&b=2)\n![HTTP图](http://images.example/route.png)',
    }, origin))
    expect(doc.title).toBe('A & B <研究>')
    const paragraphTexts = [...doc.querySelectorAll('p')].map((p) => p.textContent)
    expect(paragraphTexts).toContain('目标')
    expect(paragraphTexts).toContain('第二阶段')
    expect(paragraphTexts).toContain('比较 A & B')
    expect(doc.body.textContent).toContain('文字分析：zhipu/glm-5.2')
    expect(doc.body.textContent).toContain('架构图：sensenova/u1')
    expect([...doc.querySelectorAll('img')].map((img) => img.getAttribute('src'))).toEqual([
      `${origin}/generated/route.png`,
      'https://images.example/route.png?a=1&b=2',
      'http://images.example/route.png',
    ])
  })

  it('exports the deterministic structure when the image model returned HTTP 404', () => {
    const architecture = { title: '交通流缺失修复', layers: [
      { name: '数据准备', modules: [{ name: '时间划分' }, { name: '缺失掩码' }] },
      { name: '严格评估', modules: [{ name: '误差计算' }], formula: 'Eval = O ∩ H' },
    ] }
    const analysis = `## 文字分析（zhipu/glm-5.2）\n先建立基线。\n## 研究架构图（sensenova/u1-fast）\n结构说明\n<ARCH_JSON>${JSON.stringify(architecture)}</ARCH_JSON>\n图像服务返回 HTTP 404\n## 科研阶段路线图\n完成基线后再评估。`
    const doc = parse(buildRouteDocumentHtml({ ...route, ai_analysis: analysis }, origin))
    const svg = doc.querySelector('svg')
    expect(svg).not.toBeNull()
    expect(svg?.textContent).toContain('交通流缺失修复')
    expect(svg?.textContent).toContain('缺失掩码')
    expect(svg?.textContent).toContain('Eval = O ∩ H')
    expect(svg?.style.width).toBe('100%')
    expect(svg?.style.height).toBe('auto')
    expect(svg?.style.minWidth).toBe('')
    expect(doc.body.textContent).toContain('确定性 SVG 绘制，非图像模型生成')
    expect(doc.body.textContent).toContain('图像服务返回 HTTP 404')
    expect(doc.body.textContent).toContain('完成基线后再评估')
    expect(doc.body.textContent).not.toContain('ARCH_JSON')
    expect(doc.body.textContent).not.toContain('"modules"')
    expect(doc.querySelectorAll('img')).toHaveLength(0)
    expect(cleanRouteMarkdown(analysis)).not.toContain('"layers"')
  })

  it('prefers explicit architecture data and preserves generated image references separately', () => {
    const doc = parse(buildRouteDocumentHtml({
      ...route,
      architecture_json: { title: '当前结构', layers: [{ name: '输入层', modules: [{ name: '用户数据' }] }] },
      ai_analysis: '<ARCH_JSON>{"title":"旧结构","layers":[{"name":"旧层","modules":[{"name":"旧模块"}]}]}</ARCH_JSON>\n![模型图](/generated/route.png)',
    }, origin))
    expect(doc.querySelector('svg')?.textContent).toContain('当前结构')
    expect(doc.body.textContent).not.toContain('旧结构')
    expect(doc.querySelector('img')?.getAttribute('src')).toBe(`${origin}/generated/route.png`)
  })

  it.each([
    '<ARCH_JSON>{not valid JSON}</ARCH_JSON>',
    '<ARCH_JSON>{"layers":[{"name":"空层","modules":[]}]}</ARCH_JSON>',
    '<ARCH_JSON>{"layers":"<svg onload=alert(1)>"}</ARCH_JSON>',
    '<ARCH_JSON>{"layers":[',
  ])('falls back to text and omits invalid or incomplete architecture blocks: %s', (block) => {
    const doc = parse(buildRouteDocumentHtml({ ...route, ai_analysis: `已有文字分析。\n${block}` }, origin))
    expect(doc.body.textContent).toContain('已有文字分析')
    expect(doc.body.textContent).toContain('暂无可用的结构化架构数据')
    expect(doc.body.textContent).not.toContain('ARCH_JSON')
    expect(doc.body.textContent).not.toContain('layers')
    expect(doc.querySelectorAll('svg, script, iframe, object')).toHaveLength(0)
  })

  it('treats malicious JSON field values only as SVG text and never renders supplied markup', () => {
    const malicious = '<script>alert(1)</script><image href="https://evil.example/track" onload="alert(1)" />'
    const doc = parse(buildRouteDocumentHtml({ ...route, ai_analysis: `<ARCH_JSON>${JSON.stringify({
      title: malicious,
      layers: [{ name: malicious, modules: [{ name: malicious }], formula: malicious }],
    })}</ARCH_JSON>` }, origin))
    expect(doc.querySelector('svg')?.textContent).toContain(malicious)
    expect(doc.querySelectorAll('script, image, img, iframe, object, foreignObject')).toHaveLength(0)
    expect([...doc.querySelectorAll('*')].flatMap((el) => [...el.attributes])
      .some((attr) => attr.name.startsWith('on'))).toBe(false)
  })
})
