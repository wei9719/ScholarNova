import { describe, expect, it } from 'vitest'
import { getAbstractText } from '../abstract'

describe('abstract availability', () => {
  it.each([
    null, undefined, '', ' \n\t ', '\u200b\ufeff', '<p>&nbsp;<br></p>',
    '<div>&#160;&#x20;</div>', '<script>alert(1)</script>', '<style>body { color: red }</style>',
    'No abstract available.', 'N/A', '暂无摘要', '<p>Abstract unavailable</p>',
  ])('treats blank and placeholder metadata as unavailable: %s', abstract => {
    expect(getAbstractText(abstract)).toBe('')
  })

  it('keeps real abstract text and decodes markup without mounting HTML', () => {
    expect(getAbstractText('<jats:p>A &amp; B are compared.</jats:p>')).toBe('A & B are compared.')
    expect(getAbstractText('No abstract method can solve every problem.')).toBe('No abstract method can solve every problem.')
  })

  it.each([
    'We find x<y with a significant effect.',
    'X<Y and Z>1 are constraints.',
    'a<b and c>d are constraints.',
    'w<i and j>k are constraints.',
    'p<0.05 confirmed this.',
  ])('preserves plain-text mathematical comparisons: %s', abstract => {
    expect(getAbstractText(abstract)).toBe(abstract)
  })

  it('decodes comparison entities once without treating the decoded text as HTML', () => {
    expect(getAbstractText('We find x&lt;y and z&gt;1.')).toBe('We find x<y and z>1.')
    expect(getAbstractText('<jats:p>We find x&lt;y and z&gt;1.</jats:p>')).toBe('We find x<y and z>1.')
    expect(getAbstractText('&lt;p&gt;x&lt;y&lt;/p&gt;')).toBe('<p>x<y</p>')
  })

  it('preserves JATS and HTML paragraph boundaries, line breaks and comparisons within blocks', () => {
    expect(getAbstractText('<jats:p>Background</jats:p><jats:p>Methods: x<y.</jats:p>'))
      .toBe('Background\n\nMethods: x<y.')
    expect(getAbstractText('<p class="abstract">Background<br>Context</p><p><strong>Methods</strong></p>'))
      .toBe('Background\nContext\n\nMethods')
    expect(getAbstractText('Background\n\nMethods')).toBe('Background\n\nMethods')
  })
})
