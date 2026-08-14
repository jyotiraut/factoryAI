# One Secrets Manager secret per environment, holding every credential shared/config.py's
# Settings tree reads via SecretStr as a single JSON blob — mirroring how the app already
# reads them (four independent SecretStr fields across DatabaseSettings/AuthSettings/
# StorageSettings), not one secret per key, which would multiply this module's resource
# count for no operational benefit at this scale.

resource "aws_secretsmanager_secret" "this" {
  name                    = var.secret_name
  recovery_window_in_days = var.recovery_window_days

  tags = merge(var.tags, { Environment = var.environment })
}

resource "aws_secretsmanager_secret_version" "this" {
  secret_id     = aws_secretsmanager_secret.this.id
  secret_string = jsonencode(var.secret_values)
}
