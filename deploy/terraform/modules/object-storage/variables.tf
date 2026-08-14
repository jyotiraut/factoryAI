variable "bucket_name" {
  description = "Bucket name. Must be globally unique across all of AWS, per S3's own naming rules."
  type        = string
}

variable "environment" {
  description = "Deployment environment, e.g. staging/production. Applied as a tag, not part of the bucket name, so the same module invocation can rename the bucket without an environment rename too."
  type        = string
}

variable "versioning_enabled" {
  description = "Keep prior object versions. On for every bucket by default: ADR-0003's content-addressed keys make overwrite rare, but a bad ingest or a bug in the compensating-delete path (Phase 3) should still be recoverable."
  type        = bool
  default     = true
}

variable "noncurrent_version_expiration_days" {
  description = "Days a noncurrent (superseded) object version is kept before S3 deletes it. Bounds the cost of versioning without disabling the safety net."
  type        = number
  default     = 90
}

variable "abort_incomplete_multipart_upload_days" {
  description = "Days an incomplete multipart upload is left before S3 aborts it and reclaims the storage."
  type        = number
  default     = 7
}

variable "tags" {
  description = "Tags applied to the bucket, merged with the module's own environment tag."
  type        = map(string)
  default     = {}
}
