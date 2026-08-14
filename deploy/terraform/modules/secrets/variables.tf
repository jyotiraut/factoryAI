variable "secret_name" {
  description = "Secrets Manager secret name, e.g. factoryai/staging/app."
  type        = string
}

variable "environment" {
  description = "Deployment environment, applied as a tag."
  type        = string
}

variable "recovery_window_days" {
  description = "Days Secrets Manager waits before permanently deleting this secret after a `terraform destroy`. 0 deletes immediately — useful for a throwaway environment, never for one holding a real production password."
  type        = number
  default     = 30
}

# One key per credential the app's shared/config.py actually reads via SecretStr, so this
# module's shape is dictated by Settings, not invented independently:
#   - POSTGRES_PASSWORD  -> DatabaseSettings.password
#   - JWT_SECRET_KEY     -> AuthSettings.secret_key
#   - STORAGE_ACCESS_KEY  -> StorageSettings.access_key (S3 backend; s3_compatible.py's access_key)
#   - STORAGE_SECRET_KEY  -> StorageSettings.secret_key (S3 backend; s3_compatible.py's secret_key)
# Values are never given defaults — every one of them must be supplied at apply time (a
# generated password, or the real IAM user's access/secret key pair), never hardcoded here.
variable "secret_values" {
  description = "Map of key to value, stored as one JSON-encoded Secrets Manager secret. Keys should match the env var names the app reads directly (POSTGRES_PASSWORD, JWT_SECRET_KEY, STORAGE_ACCESS_KEY, STORAGE_SECRET_KEY) so a consumer (e.g. an External Secrets Operator sync into Kubernetes) can project each key straight into the container's environment unchanged."
  type        = map(string)
  sensitive   = true

  validation {
    condition     = length(var.secret_values) > 0
    error_message = "secret_values must not be empty — an empty secret defeats the point of this module."
  }
}

variable "tags" {
  description = "Tags applied to the secret, merged with the module's own environment tag."
  type        = map(string)
  default     = {}
}
