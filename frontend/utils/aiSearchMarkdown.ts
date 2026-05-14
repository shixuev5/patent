import MarkdownIt from 'markdown-it'

type MarkdownItLinkOpenRule = NonNullable<typeof renderer.renderer.rules.link_open>

const renderer = new MarkdownIt({
  html: false,
  breaks: true,
  linkify: true,
})

const defaultLinkOpen: MarkdownItLinkOpenRule = renderer.renderer.rules.link_open || ((tokens, idx, options, _env, self) => self.renderToken(tokens, idx, options))

renderer.renderer.rules.link_open = (tokens, idx, options, env, self) => {
  const token = tokens[idx]
  token.attrSet('target', '_blank')
  token.attrSet('rel', 'noopener noreferrer')
  return defaultLinkOpen(tokens, idx, options, env, self)
}

export const aiSearchMarkdownRenderer = renderer
