-- EDITH — Reddit Growth & Networking Assistant
-- Supabase (PostgreSQL) şeması. Yeni ve BOŞ bir Supabase projesinde çalıştır.
--
-- Tasarım kararı: hiçbir tablo Reddit'e kilitli değil. `platform` sütunu ve
-- platform-nötr adlandırma (community — subreddit değil) sayesinde Instagram/
-- TikTok eklenirken şema göçü gerekmez.
--
-- Erişim modeli: tüm okuma/yazma sunucu tarafından service_role anahtarıyla
-- yapılır. Tarayıcı Supabase'e hiç bağlanmaz, bu yüzden her tabloda RLS açık ve
-- politika YOK — anon anahtarla kimse hiçbir şey okuyamaz. (service_role RLS'i
-- baypas eder; bilinçli tercih.)

-- ---------------------------------------------------------------- enum'lar
create type platform_kind    as enum ('reddit', 'instagram', 'facebook', 'tiktok');
create type opportunity_kind as enum ('post', 'comment', 'reply');
create type action_kind      as enum ('ENGAGE', 'NETWORK', 'APP_SHARE', 'SKIP');
create type opp_status       as enum ('pending', 'approved', 'rejected', 'posted', 'failed');
create type lesson_status    as enum ('proposed', 'active', 'retired');
create type lesson_source    as enum ('boss', 'ai_inference');
create type aggression_kind  as enum ('temkinli', 'dengeli', 'atak');

-- ---------------------------------------------------------- opportunities
-- Yakalanan her fırsat adayı. Feed'in kaynağı.
create table opportunities (
  id                uuid primary key default gen_random_uuid(),
  platform          platform_kind    not null default 'reddit',
  community         text             not null,          -- 'stoicism' (r/ öneki yok)
  kind              opportunity_kind not null,
  external_id       text             not null,          -- platformun kendi id'si
  source_url        text             not null,
  source_title      text,
  source_text       text,
  source_author     text,
  source_created_at timestamptz,

  -- Skorlar (0-100). Hepsi AI'dan gelir, zod ile doğrulanır.
  relevance_score   int,
  network_score     int,
  promotion_score   int,
  spam_risk         int,
  community_fit     int,
  magnetism_score   int,   -- çekim gücü: bu profile tıklatır mı
  momentum_score    int,   -- thread ne kadar taze/hızlı yükseliyor

  ai_action         action_kind,     -- AI'ın ham önerisi
  final_action      action_kind,     -- kurallar motorundan geçmiş hâli
  blocked_reason    text,            -- final_action düşürüldüyse nedeni
  ai_reason         text,
  ai_draft          text,

  -- EDITH bilmediği kişisel detaya ihtiyaç duyduysa: uydurmaz, sorar.
  missing_info      text[],

  status            opp_status not null default 'pending',
  posted_url        text,
  posted_at         timestamptz,
  error_message     text,

  created_at        timestamptz not null default now(),
  updated_at        timestamptz not null default now(),

  -- Aynı içeriği iki kez analiz etme (ucuz filtrenin dayanağı)
  unique (platform, external_id)
);

create index on opportunities (status, created_at desc);
create index on opportunities (platform, community);

-- ------------------------------------------------------------- communities
-- Topluluk profili — zamanla dolar, statik liste değil.
create table communities (
  id                   uuid primary key default gen_random_uuid(),
  platform             platform_kind not null default 'reddit',
  name                 text not null,
  category             text,
  language             text default 'en',
  active               boolean not null default true,

  self_promo_tolerance text,          -- 'none' | 'low' | 'medium' | 'high'
  community_fit        int,
  engagement_quality   int,
  preferred_actions    action_kind[],

  rules_cache          jsonb,         -- /about/rules ham hâli
  rules_fetched_at     timestamptz,
  rules_summary        text,          -- AI'ın özeti (kurallar motoru bunu okur)

  last_scanned_at      timestamptz,
  created_at           timestamptz not null default now(),

  unique (platform, name)
);

-- ---------------------------------------------------------------- contacts
-- Hafif CRM. YALNIZCA herkese açık veri — özel bilgi toplanmaz.
create table contacts (
  id                  uuid primary key default gen_random_uuid(),
  platform            platform_kind not null default 'reddit',
  username            text not null,
  community           text,
  public_interests    text[],
  interaction_count   int not null default 0,
  last_interaction_at timestamptz,
  potential           text,           -- 'low' | 'medium' | 'high'
  notes               text,
  created_at          timestamptz not null default now(),

  unique (platform, username)
);

-- ----------------------------------------------------------- decisions_log
-- Öğrenmenin HAM sinyali. En değerli sütun: draft_before/draft_after farkı —
-- patronun üslubu buradan öğrenilir.
create table decisions_log (
  id             uuid primary key default gen_random_uuid(),
  opportunity_id uuid references opportunities(id) on delete cascade,
  ai_action      action_kind,
  user_action    text,        -- 'approve' | 'edit' | 'skip'
  draft_before   text,
  draft_after    text,

  -- Gerçek sonuç — monitor.yml doldurur.
  outcome_score     int,      -- upvote
  outcome_replies   int,
  outcome_removed   boolean,  -- mod sildi mi (en güçlü negatif sinyal)
  outcome_checked_at timestamptz,

  created_at     timestamptz not null default now()
);

create index on decisions_log (created_at desc);

-- ----------------------------------------------------------------- lessons
-- KURALLAR — kalıcı bellek. "Bu 6 ay sonra da doğru olacak mı?" → evet.
-- status='proposed' olan hiçbir ders prompt'a girmez; patron onaylamalı.
create table lessons (
  id            uuid primary key default gen_random_uuid(),
  text          text not null,
  category      text,                  -- ton | strateji | platform-kurali | iletisim
  source        lesson_source not null,
  status        lesson_status not null default 'proposed',
  evidence      text,                  -- ai_inference ise dayandığı veri
  times_applied int not null default 0,
  created_at    timestamptz not null default now(),
  approved_at   timestamptz
);

create index on lessons (status);

-- ------------------------------------------------------------------- facts
-- OLGULAR — tarihli, bayatlayan bellek. "6 ay sonra doğru olmayabilir" → buraya.
-- Bu ayrım şema seviyesinde: kural ile olgunun karışması bu projede yaşanmış
-- bir hatanın kaynağıydı (bkz. CLAUDE.md öğrenme protokolü).
create table facts (
  id           uuid primary key default gen_random_uuid(),
  text         text not null,
  scope        text,                   -- 'r/indiedev' | 'genel' | ...
  as_of        date not null default current_date,
  stale_after  date,                   -- bu tarihten sonra prompt'a girmez
  created_at   timestamptz not null default now()
);

-- ---------------------------------------------------------------- settings
-- Tek satır. id=1 sabit.
create table settings (
  id                       int primary key default 1,
  aggression_level         aggression_kind not null default 'dengeli',
  ai_provider              text not null default 'deepseek',
  max_app_share_per_week   int not null default 1,   -- topluluk başına
  max_comments_per_day     int not null default 5,   -- toplam
  paused                   boolean not null default false,
  updated_at               timestamptz not null default now(),
  constraint settings_singleton check (id = 1)
);

insert into settings (id) values (1);

-- ----------------------------------------------------------- chat_messages
create table chat_messages (
  id         uuid primary key default gen_random_uuid(),
  role       text not null,            -- 'user' | 'assistant'
  content    text not null,
  created_at timestamptz not null default now()
);

create index on chat_messages (created_at);

-- --------------------------------------------------------------------- RLS
-- Her tabloda açık, politika yok → anon anahtar hiçbir şey göremez.
-- Sunucu service_role ile bağlanır ve RLS'i baypas eder.
alter table opportunities  enable row level security;
alter table communities    enable row level security;
alter table contacts       enable row level security;
alter table decisions_log  enable row level security;
alter table lessons        enable row level security;
alter table facts          enable row level security;
alter table settings       enable row level security;
alter table chat_messages  enable row level security;

-- ------------------------------------------------- başlangıç topluluk seti
-- Hedef topluluklar. Kuralları ilk taramada çekilip cache'lenir.
insert into communities (platform, name, category, self_promo_tolerance) values
  ('reddit', 'Stoicism',           'philosophy',  'low'),
  ('reddit', 'philosophy',         'philosophy',  'none'),
  ('reddit', 'getdisciplined',     'self-improvement', 'low'),
  ('reddit', 'selfimprovement',    'self-improvement', 'low'),
  ('reddit', 'DecidingToBeBetter', 'self-improvement', 'low'),
  ('reddit', 'iOSProgramming',     'dev',         'medium'),
  ('reddit', 'indiedev',           'dev',         'medium'),
  ('reddit', 'SideProject',        'dev',         'high');
