export interface GenerateParams {
  prompt: string
  negative_prompt: string
  model: string
  width: number
  height: number
  steps: number
  scale: number
  sampler: string
  seed: number | null
  n_samples: number
}

export interface GenerateResponse {
  mock: boolean
  count: number
  images: string[] // base64-encoded PNGs
}

export interface GenResult {
  id: number
  url: string // data URL
  params: GenerateParams
  mock: boolean
}
