-- T29: each chat is bound to one client scope when it starts; later requests must match.

CREATE TABLE brain.chat_bindings (
    chat_id     text PRIMARY KEY,
    scope       text NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now()
);
