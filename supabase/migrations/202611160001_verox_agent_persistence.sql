-- Verox persistent cognitive state for Supabase project LordVerox.
CREATE TABLE IF NOT EXISTS public.verox_conversations (
    conversation_id text PRIMARY KEY,
    state jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS public.verox_messages (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    conversation_id text NOT NULL REFERENCES public.verox_conversations(conversation_id) ON DELETE CASCADE,
    role text NOT NULL CHECK (role IN ('user', 'assistant', 'system')),
    content text NOT NULL,
    model text NOT NULL DEFAULT 'unknown',
    status text NOT NULL DEFAULT 'complete' CHECK (status IN ('complete', 'failed', 'interrupted')),
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS verox_messages_conversation_id_id_idx
    ON public.verox_messages (conversation_id, id DESC);
CREATE TABLE IF NOT EXISTS public.verox_memory (
    memory_key text PRIMARY KEY,
    content jsonb NOT NULL,
    namespace text NOT NULL DEFAULT 'global',
    source text NOT NULL DEFAULT 'runtime',
    importance smallint NOT NULL DEFAULT 3 CHECK (importance BETWEEN 1 AND 5),
    active boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE public.verox_conversations ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.verox_messages ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.verox_memory ENABLE ROW LEVEL SECURITY;
