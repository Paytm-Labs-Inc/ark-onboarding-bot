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
{{- if eq .Values.web.forwardedAllowIps "*" -}}
{{- fail "web.forwardedAllowIps='*' trusts every peer's X-Forwarded-* headers (any client can mint its own rate-limit key); the app refuses it at startup, so refuse it here." -}}
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
{{- end -}}
