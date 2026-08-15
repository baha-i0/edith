/**
 * AI sağlayıcı soyutlaması.
 *
 * Tek bir sağlayıcıya kilitlenmemek bilinçli bir karar: model kalitesi ve
 * fiyatlandırma hızla değişiyor. Kodun geri kalanı yalnızca bu arayüzü tanır,
 * sağlayıcı `AI_PROVIDER` ortam değişkeniyle değişir.
 */
export interface AIProvider {
  readonly name: string;
  complete(input: CompletionInput): Promise<string>;
}

export interface CompletionInput {
  system: string;
  user: string;
  /** JSON çıktısı bekleniyorsa true — sağlayıcı destekliyorsa json modu açılır. */
  json?: boolean;
  maxTokens?: number;
  temperature?: number;
}

/**
 * OpenAI-uyumlu sohbet uç noktası. DeepSeek, Qwen, OpenRouter ve OpenCode'un
 * tamamı bu şemayı konuşuyor; tek fark taban adres ve model adı.
 */
export class OpenAICompatibleProvider implements AIProvider {
  constructor(
    readonly name: string,
    private readonly baseUrl: string,
    private readonly apiKey: string,
    private readonly model: string
  ) {}

  async complete(input: CompletionInput): Promise<string> {
    const res = await fetch(`${this.baseUrl}/chat/completions`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${this.apiKey}`,
      },
      body: JSON.stringify({
        model: this.model,
        messages: [
          { role: "system", content: input.system },
          { role: "user", content: input.user },
        ],
        temperature: input.temperature ?? 0.7,
        max_tokens: input.maxTokens ?? 1200,
        ...(input.json ? { response_format: { type: "json_object" } } : {}),
      }),
    });

    if (!res.ok) {
      throw new Error(`AI çağrısı başarısız (${res.status}): ${await res.text()}`);
    }

    const json = (await res.json()) as {
      choices: { message: { content: string } }[];
    };
    const content = json.choices?.[0]?.message?.content;
    if (!content) throw new Error("AI boş yanıt döndü");
    return content;
  }
}

let cached: AIProvider | null = null;

export function getProvider(): AIProvider {
  if (cached) return cached;

  const name = process.env.AI_PROVIDER ?? "deepseek";
  const apiKey = process.env.AI_API_KEY;
  if (!apiKey) throw new Error("Eksik ortam değişkeni: AI_API_KEY");

  // Varsayılanlar yalnızca kolaylık; ikisi de env'den ezilebilir.
  const defaults: Record<string, { baseUrl: string; model: string }> = {
    deepseek: { baseUrl: "https://api.deepseek.com/v1", model: "deepseek-v4-flash" },
    qwen: {
      baseUrl: "https://dashscope.aliyuncs.com/compatible-mode/v1",
      model: "qwen3.7-flash",
    },
  };

  const fallback = defaults[name] ?? defaults.deepseek;
  cached = new OpenAICompatibleProvider(
    name,
    process.env.AI_BASE_URL ?? fallback.baseUrl,
    apiKey,
    process.env.AI_MODEL ?? fallback.model
  );
  return cached;
}
