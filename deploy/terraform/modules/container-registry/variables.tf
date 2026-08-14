variable "repository_name" {
  description = "ECR repository name, e.g. factoryai/api. Phase 9-14 introduced the first real application image (deploy/docker/factoryai.Dockerfile) that this repository is meant to hold."
  type        = string
}

variable "environment" {
  description = "Deployment environment, applied as a tag."
  type        = string
}

variable "image_tag_mutability" {
  description = "IMMUTABLE prevents a tag (e.g. a Git SHA tag from CI) from ever being silently overwritten — the same non-negotiable ADR-0006/ADR-0004 already apply to dataset and model artifact identity."
  type        = string
  default     = "IMMUTABLE"
}

variable "scan_on_push" {
  description = "Enable ECR's basic image scanning on every push. Phase 14's own CI/CD scope already runs Trivy in the pipeline; this is a second, registry-side check that also covers images pushed outside that pipeline."
  type        = bool
  default     = true
}

variable "untagged_image_expiry_days" {
  description = "Days an untagged image (superseded by a re-push of the same tag, or a failed multi-stage build) is kept before the lifecycle policy expires it."
  type        = number
  default     = 14
}

variable "max_tagged_images" {
  description = "Maximum number of tagged images retained, oldest pushed first expired. Keeps a long-lived repository from growing unbounded across years of CI runs."
  type        = number
  default     = 30
}

variable "tags" {
  description = "Tags applied to the repository, merged with the module's own environment tag."
  type        = map(string)
  default     = {}
}
