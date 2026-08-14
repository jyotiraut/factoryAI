{{/*
Fully qualified name, respecting an explicit `.Values.fullnameOverride` when set.
*/}}
{{- define "factoryai.fullname" -}}
{{- .Values.fullnameOverride | default .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{/*
Common labels applied to every resource this chart manages.
*/}}
{{- define "factoryai.labels" -}}
app.kubernetes.io/name: {{ .Chart.Name }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end -}}

{{/*
Selector labels for a given component (api, worker, postgresql, redis, minio, mlflow).
*/}}
{{- define "factoryai.selectorLabels" -}}
app.kubernetes.io/name: {{ .Chart.Name }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/component: {{ .component }}
{{- end -}}

{{/*
Postgres host: the in-cluster Service name when bundled, else the operator-supplied
external endpoint (an RDS address from `deploy/terraform`, typically).
*/}}
{{- define "factoryai.postgresHost" -}}
{{- if .Values.postgresql.enabled -}}
{{ include "factoryai.fullname" . }}-postgresql
{{- else -}}
{{ required "postgresql.external.host is required when postgresql.enabled is false" .Values.postgresql.external.host }}
{{- end -}}
{{- end -}}

{{/*
Redis host, same bundled-vs-external logic as Postgres above.
*/}}
{{- define "factoryai.redisHost" -}}
{{- if .Values.redis.enabled -}}
{{ include "factoryai.fullname" . }}-redis
{{- else -}}
{{ required "redis.external.host is required when redis.enabled is false" .Values.redis.external.host }}
{{- end -}}
{{- end -}}

{{/*
Object storage endpoint, same bundled-vs-external logic.
*/}}
{{- define "factoryai.storageEndpoint" -}}
{{- if .Values.minio.enabled -}}
http://{{ include "factoryai.fullname" . }}-minio:9000
{{- else -}}
{{ required "minio.external.endpoint is required when minio.enabled is false" .Values.minio.external.endpoint }}
{{- end -}}
{{- end -}}

{{/*
MLflow tracking URI, same bundled-vs-external logic.
*/}}
{{- define "factoryai.mlflowUri" -}}
{{- if .Values.mlflow.enabled -}}
http://{{ include "factoryai.fullname" . }}-mlflow:5000
{{- else -}}
{{ required "mlflow.external.trackingUri is required when mlflow.enabled is false" .Values.mlflow.external.trackingUri }}
{{- end -}}
{{- end -}}
