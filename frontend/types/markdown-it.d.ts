declare module 'markdown-it' {
  interface MarkdownItToken {
    attrSet(name: string, value: string): void
  }

  interface MarkdownItRendererRule {
    (
      tokens: MarkdownItToken[],
      idx: number,
      options: unknown,
      env: unknown,
      self: {
        renderToken(tokens: MarkdownItToken[], idx: number, options: unknown): string
      },
    ): string
  }

  interface MarkdownItRenderer {
    rules: Record<string, MarkdownItRendererRule | undefined>
  }

  interface MarkdownItOptions {
    html?: boolean
    breaks?: boolean
    linkify?: boolean
  }

  export default class MarkdownIt {
    constructor(options?: MarkdownItOptions)
    renderer: MarkdownItRenderer
  }
}
