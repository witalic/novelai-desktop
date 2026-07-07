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

export type PanelParams = Omit<GenerateParams, 'prompt' | 'negative_prompt'>

export interface GenerateResponse {
  mock: boolean
  count: number
  images: string[] // base64-encoded PNGs
}

export type StreamEvent =
  | { type: 'intermediate'; samp: number; step: number; mime: string; image: string }
  | { type: 'final'; mime: string; image: string }
  | { type: 'error'; message: string; status: number }

export interface GenResult {
  id: number
  url: string // data URL
  params: GenerateParams
  mock: boolean
}
