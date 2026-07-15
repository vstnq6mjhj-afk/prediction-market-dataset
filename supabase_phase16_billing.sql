-- Phase 16: Stripe subscriptions, term tracking, and webhook idempotency
-- Run in Supabase SQL Editor before enabling Stripe checkout.

alter table public.api_keys
    add column if not exists stripe_customer_id text;

alter table public.api_keys
    add column if not exists stripe_subscription_id text;

alter table public.api_keys
    add column if not exists stripe_price_id text;

alter table public.api_keys
    add column if not exists billing_term text;

alter table public.api_keys
    add column if not exists subscription_status text
    default 'free';

alter table public.api_keys
    add column if not exists current_period_start timestamptz;

alter table public.api_keys
    add column if not exists current_period_end timestamptz;

alter table public.api_keys
    add column if not exists cancel_at_period_end boolean
    default false;

create unique index if not exists
    api_keys_stripe_customer_id_unique
on public.api_keys (stripe_customer_id)
where stripe_customer_id is not null;

create unique index if not exists
    api_keys_stripe_subscription_id_unique
on public.api_keys (stripe_subscription_id)
where stripe_subscription_id is not null;

create table if not exists public.stripe_webhook_events (
    event_id text primary key,
    event_type text not null,
    status text not null
        check (status in ('processing', 'processed', 'failed')),
    attempt_count integer not null default 1,
    error_message text,
    processed_at timestamptz,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

alter table public.stripe_webhook_events
    enable row level security;

revoke all on table public.stripe_webhook_events
    from anon, authenticated;

grant all on table public.stripe_webhook_events
    to service_role;
