# S3 bucket: the AWS-native equivalent of the MinIO buckets ADR-0003 already runs
# locally (factoryai-raw / factoryai-datasets / factoryai-artifacts / factoryai-heatmaps).
# One `object-storage` module call per bucket — the app's own StorageSettings.buckets
# already enumerates four independent names, not one bucket with four prefixes, so the
# Terraform shape mirrors the application shape rather than inventing a new one.

resource "aws_s3_bucket" "this" {
  bucket = var.bucket_name

  tags = merge(
    var.tags,
    {
      Environment = var.environment
      ManagedBy   = "terraform"
    }
  )
}

# Block-public-access: on unconditionally. Nothing in this platform's design (ADR-0003's
# port is reached only through presigned URLs) ever needs a public bucket or object ACL.
resource "aws_s3_bucket_public_access_block" "this" {
  bucket = aws_s3_bucket.this.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_versioning" "this" {
  bucket = aws_s3_bucket.this.id

  versioning_configuration {
    status = var.versioning_enabled ? "Enabled" : "Suspended"
  }
}

# SSE-S3 (AES256): no customer-managed KMS key exists for this environment yet, and the
# app's ObjectStore port never expects a KMS key ARN (see s3_compatible.py's client
# construction) — adding one here would be a Terraform-only concern the app can't act on.
resource "aws_s3_bucket_server_side_encryption_configuration" "this" {
  bucket = aws_s3_bucket.this.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "this" {
  # Depends on versioning being applied first — a lifecycle rule referencing noncurrent
  # versions on a not-yet-versioned bucket is accepted by the API but meaningless.
  depends_on = [aws_s3_bucket_versioning.this]

  bucket = aws_s3_bucket.this.id

  rule {
    id     = "expire-noncurrent-versions"
    status = "Enabled"

    # Empty filter: applies to every object in the bucket. The provider requires exactly
    # one of `filter`/`prefix` to be set explicitly now that a bare rule with neither is
    # deprecated.
    filter {}

    noncurrent_version_expiration {
      noncurrent_days = var.noncurrent_version_expiration_days
    }

    abort_incomplete_multipart_upload {
      days_after_initiation = var.abort_incomplete_multipart_upload_days
    }
  }
}
