import { defineStore } from 'pinia'

export type Phase = 'idle' | 'connecting' | 'streaming' | 'done' | 'error'

export interface StageEvent {
  seq: number
  name: string
  status: string
  duration_ms?: number
}

export interface Citation {
  no: number
  doc: string
  volume: string
  chunk_id: string
  quote: string
  supports: string
  page: string
  ocr: boolean
}

export interface Refusal {
  reason_code: string
  message: string
  suggestions: string[]
}

export interface StreamingMsg {
  messageId: string | null
  phase: Phase
  stages: StageEvent[]
  answerType: string | null
  citations: Citation[]
  text: string
  caveats: string[]
  corrections: unknown[]
  refusal: Refusal | null
  selectedCite: number
}

export const useChatStore = defineStore('chat', {
  state: () => ({
    conversationId: null as string | null,
    messages: [] as StreamingMsg[],
    current: null as StreamingMsg | null,
  }),
  actions: {
    startStreaming() {
      this.current = {
        messageId: null,
        phase: 'connecting',
        stages: [],
        answerType: null,
        citations: [],
        text: '',
        caveats: [],
        corrections: [],
        refusal: null,
        selectedCite: 1,
      }
    },
    selectCite(no: number) {
      if (this.current) this.current.selectedCite = no
    },
  },
})
