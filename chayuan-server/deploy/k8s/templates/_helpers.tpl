{{/* Chayuan helm helpers */}}

{{- define "chayuan.fullname" -}}
{{- default .Chart.Name .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "chayuan.gateway.fullname" -}}
{{- printf "%s-gateway" (include "chayuan.fullname" .) | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "chayuan.server.fullname" -}}
{{- printf "%s-server" (include "chayuan.fullname" .) | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "chayuan.labels" -}}
app.kubernetes.io/name: {{ .Chart.Name }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" }}
{{- end -}}

{{- define "chayuan.gateway.selectorLabels" -}}
app.kubernetes.io/name: {{ .Chart.Name }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/component: gateway
{{- end -}}

{{- define "chayuan.server.selectorLabels" -}}
app.kubernetes.io/name: {{ .Chart.Name }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/component: server
{{- end -}}

{{- define "chayuan.image" -}}
{{- $registry := default .Values.global.registry "docker.io" -}}
{{- printf "%s/%s:%s" $registry .repository (default "latest" .tag) -}}
{{- end -}}
