export type ChangelogCategory = 'feature' | 'improvement' | 'fix' | 'breaking'

export interface ChangelogItem {
  type: ChangelogCategory
  text: string
}

export interface ChangelogRelease {
  version: string
  date: string
  title?: string
  items: ChangelogItem[]
}

export interface ChangelogApiResponse {
  source: string
  format: string
  total: number
  releases: ChangelogRelease[]
}
