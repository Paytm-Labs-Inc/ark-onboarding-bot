{{/* Secret the pods read their env from: out-of-band existingSecret, else the one the ExternalSecret owns. */}}
{{- define "ark-onboarding-bot.secretName" -}}
{{- if .Values.externalSecret.existingSecret -}}{{ .Values.externalSecret.existingSecret }}{{- else -}}{{ .Values.externalSecret.secretName }}{{- end -}}
{{- end -}}

{{/*
Fail-closed guards. Each one turns a launch-review finding into a render error,
so the wrong config cannot ship green. Returns nothing on success.
*/}}
{{- define "ark-onboarding-bot.guards" -}}
{{- if not .Values.image.tag -}}
{{- fail "image.tag is required: argocd-image-updater writes it into the overlay after the first ECR publish. A chart with no tag would deploy nothing anyone chose." -}}
{{- end -}}
{{- if and .Values.ingress.enabled (not .Values.web.forwardedAllowIps) -}}
{{- fail "web.forwardedAllowIps is required when ingress.enabled: set it to the nginx-ingress controller CIDR. Without it the rate limit is per-ingress and the login cookie loses Secure behind TLS termination." -}}
{{- end -}}
{{- if and (gt (int .Values.replicas) 1) (not .Values.stateful.multiReplicaAcknowledged) -}}
{{- fail "replicas > 1 needs sticky sessions and a durable query-log sink first (launch review, blocker 5): chat sessions and the answer cache are in-process. Set stateful.multiReplicaAcknowledged=true only once that is done." -}}
{{- end -}}
{{- if and .Values.externalSecret.enabled (not .Values.externalSecret.existingSecret) (not .Values.externalSecret.awsSecretPath) -}}
{{- fail "externalSecret.awsSecretPath is required (or set externalSecret.existingSecret): the pod needs PI_API_KEY and ARK_ACCESS_TOKEN from somewhere, and nothing secret lives in this chart." -}}
{{- end -}}
{{- if and .Values.ingress.authGate.enabled (not .Values.ingress.authGate.authUrl) -}}
{{- fail "ingress.authGate.authUrl is required when the gate is enabled: nginx-ingress drops an empty auth-url and admits the Ingress anyway, a gate that fails OPEN." -}}
{{- end -}}
{{- if and .Values.ingress.authGate.enabled (not .Values.ingress.authGate.signinUrl) -}}
{{- fail "ingress.authGate.signinUrl is required when the gate is enabled, else the login redirect has nowhere to go." -}}
{{- end -}}
{{- if and .Values.redis.enabled (ne (lower (.Values.web.env.SESSION_STORE | default "")) "redis") -}}
{{- fail "redis.enabled without SESSION_STORE=redis in web.env: the pod would run a Redis it never connects to, and chats would still vanish on restart while the dashboard shows a healthy Redis. An absent key, an empty value and 'memory' all land here. Set SESSION_STORE=redis and REDIS_URL." -}}
{{- end -}}
{{- /*
  Keyed on what the app will actually DO, not on whether this chart happens to
  run the server. values.yaml documents pointing REDIS_URL at an existing Redis
  with enabled:false -- that path had no guard at all while this one keyed on
  redis.enabled. Lowercased because the app lowercases; an equality test alone
  would refuse "Redis", which fails closed but surprises.
*/ -}}
{{- if and (eq (lower (.Values.web.env.SESSION_STORE | default "")) "redis") (not (.Values.web.env.REDIS_URL | default "")) -}}
{{- fail "SESSION_STORE=redis with no non-empty REDIS_URL in web.env: build_session_store raises on the missing URL, and /ready never touches the session store -- so the pod goes READY and STAYS there while /api/sessions 500s and the ask path returns 502 'upstream unavailable', pointing whoever debugs it at the gateway instead of at this file. Set REDIS_URL." -}}
{{- end -}}
{{- end -}}
