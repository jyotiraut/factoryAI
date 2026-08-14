terraform {
  required_version = ">= 1.5"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }

  # No backend configured: this environment has never been applied against a real AWS
  # account (see deploy/terraform/README.md), so there is no S3/DynamoDB state backend to
  # point at yet. Whoever runs this for real should add a `backend "s3" { ... }` block here
  # before the first apply, not after.
}

provider "aws" {
  region = var.aws_region
}
