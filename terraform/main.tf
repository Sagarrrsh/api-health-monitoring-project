terraform {
  required_version = ">= 1.0"
  
  # IMPORTANT: Configure backend AFTER creating S3 bucket and DynamoDB table
  # Comment out this block for first run, then uncomment after backend setup
  # See backend-setup.sh for automated setup
  
  # backend "s3" {
  #   bucket         = "your-username-terraform-state"
  #   key            = "health-monitor/terraform.tfstate"
  #   region         = "us-east-1"
  #   encrypt        = true
  #   dynamodb_table = "terraform-state-lock"
  # }
  
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Project     = var.project_name
      ManagedBy   = "Terraform"
      Environment = "demo"
    }
  }
}