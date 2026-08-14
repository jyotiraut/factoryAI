output "bucket_name" {
  description = "Bucket name — feeds the app's STORAGE_BUCKET_* settings (shared/config.py's StorageSettings)."
  value       = aws_s3_bucket.this.id
}

output "bucket_arn" {
  description = "Bucket ARN, for IAM policies granting the application's task role access."
  value       = aws_s3_bucket.this.arn
}

output "bucket_regional_domain_name" {
  description = "Regional S3 endpoint (bucket.s3.<region>.amazonaws.com). The app's STORAGE_ENDPOINT expects a scheme-qualified URL, e.g. https://<this value>; s3_compatible.py's boto3 client also accepts the bare regional endpoint via STORAGE_REGION plus the AWS SDK's own endpoint resolution, so either form works."
  value       = aws_s3_bucket.this.bucket_regional_domain_name
}
