export type Platform = "reddit" | "instagram" | "facebook" | "tiktok";
export type OpportunityKind = "post" | "comment" | "reply";
export type Action = "ENGAGE" | "NETWORK" | "APP_SHARE" | "SKIP";
export type OppStatus = "pending" | "approved" | "rejected" | "posted" | "failed";
export type AggressionLevel = "temkinli" | "dengeli" | "atak";

/** Bir platformdan ham olarak çekilen içerik — henüz analiz edilmemiş. */
export interface RawItem {
  platform: Platform;
  community: string;
  kind: OpportunityKind;
  externalId: string;
  url: string;
  title?: string;
  text?: string;
  author?: string;
  createdAt: Date;
  score: number;
  numComments: number;
  nsfw: boolean;
  /** Bizim hesabımız bu thread'e daha önce yanıt verdi mi (platform biliyorsa). */
  alreadyReplied?: boolean;
}

/** AI'ın bir fırsat için ürettiği yapılandırılmış analiz. */
export interface Analysis {
  action: Action;
  relevance_score: number;
  network_score: number;
  promotion_score: number;
  spam_risk: number;
  community_fit: number;
  magnetism_score: number;
  reason: string;
  draft: string;
  /** Baha'ya ait, EDITH'in bilmediği kişisel detaylar. Doluysa taslak yayınlanamaz. */
  missing_info: string[];
}

export interface Opportunity {
  id: string;
  platform: Platform;
  community: string;
  kind: OpportunityKind;
  external_id: string;
  source_url: string;
  source_title: string | null;
  source_text: string | null;
  source_author: string | null;
  source_created_at: string | null;

  relevance_score: number | null;
  network_score: number | null;
  promotion_score: number | null;
  spam_risk: number | null;
  community_fit: number | null;
  magnetism_score: number | null;
  momentum_score: number | null;

  ai_action: Action | null;
  final_action: Action | null;
  blocked_reason: string | null;
  ai_reason: string | null;
  ai_draft: string | null;
  missing_info: string[] | null;

  status: OppStatus;
  posted_url: string | null;
  posted_at: string | null;
  error_message: string | null;
  created_at: string;
}

export interface Community {
  id: string;
  platform: Platform;
  name: string;
  category: string | null;
  language: string | null;
  active: boolean;
  self_promo_tolerance: string | null;
  community_fit: number | null;
  engagement_quality: number | null;
  preferred_actions: Action[] | null;
  rules_cache: unknown;
  rules_fetched_at: string | null;
  rules_summary: string | null;
  last_scanned_at: string | null;
}

export interface Lesson {
  id: string;
  text: string;
  category: string | null;
  source: "boss" | "ai_inference";
  status: "proposed" | "active" | "retired";
  evidence: string | null;
  times_applied: number;
  created_at: string;
}

export interface Fact {
  id: string;
  text: string;
  scope: string | null;
  as_of: string;
  stale_after: string | null;
}

export interface Settings {
  id: number;
  aggression_level: AggressionLevel;
  ai_provider: string;
  max_app_share_per_week: number;
  max_comments_per_day: number;
  paused: boolean;
}
