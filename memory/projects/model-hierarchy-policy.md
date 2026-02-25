# Model Hierarchy Policy (Quality + Cost)

Last updated: 2026-02-25 (UTC)
Owner: Islam

## Objective
Maximize capability while minimizing spend by routing tasks through the cheapest reliable model/service first, then escalating only when needed.

## Available Stack
- **Specialized services**
  - Whisper ASR endpoint (speech-to-text)
  - OCR endpoint (image/PDF text extraction)
- **General LLMs**
  - `local8081/Ministral-3-14B-Instruct-2512-Q5_K_M.gguf` (`ministral14`)
  - `openai-codex/gpt-5.3-codex`
  - `openai/gpt-5.2` (to be enabled via API key)
  - `anthropic/claude-sonnet-4-6` (to be enabled)
  - `anthropic/claude-opus-4-6` (to be enabled)

## Routing Order
1. **Task-specific service first**
   - Audio/transcription → Whisper endpoint.
   - OCR/document text extraction → OCR endpoint.
2. **Tier 1 (cheap default): Ministral14**
   - Drafting, summaries, extraction, low/medium complexity transforms.
3. **Tier 2 (strong coding/general): Codex 5.3**
   - Complex coding, debugging, multi-step tool workflows.
4. **Tier 3 (premium reasoning): Claude Sonnet 4.6**
   - High-stakes reasoning/writing, nuanced judgment.
5. **Tier 4 (max quality): Claude Opus 4.6**
   - Critical outputs, difficult ambiguity, final-pass quality.
6. **Optional branch: ChatGPT 5.2**
   - Secondary opinion / tie-breaker / alternate style.

## Escalation Triggers
Escalate one tier when any of the following occurs:
- Low confidence in correctness or completeness.
- Failed first attempt or conflicting evidence.
- User explicitly asks for “best possible” quality.
- Safety-sensitive or high-impact deliverable.

## Cost Guardrails
- Start with lower-cost tier unless user asks otherwise.
- Avoid immediate premium model jumps for routine tasks.
- Use premium models mainly for final-pass verification or critical tasks.
- Keep specialized endpoints for ASR/OCR to avoid expensive text-only model misuse.

## OpenClaw Config Notes
- Aliases added in `~/.openclaw/openclaw.json` for:
  - Codex 5.3
  - ChatGPT 5.2
  - Claude Sonnet 4.6
  - Claude Opus 4.6
  - Ministral 14B (8081)

## Remaining Enablement Steps
- Add `OPENAI_API_KEY` on VPS (for `openai/gpt-5.2`).
- Add Anthropic auth on VPS (API key or setup-token) for Sonnet/Opus.
- Restart OpenClaw gateway after credential changes.
- Verify with `openclaw models list` and live test prompts.
