###############################################################################
# RecoverAI Enterprise – Terraform Variables
###############################################################################

variable "aws_region" {
  description = "AWS region to deploy into"
  type        = string
  default     = "ap-south-1"   # Mumbai — closest to Razorpay infra
}

variable "environment" {
  description = "Deployment environment: development | staging | production"
  type        = string
  default     = "staging"
  validation {
    condition     = contains(["development", "staging", "production"], var.environment)
    error_message = "environment must be development, staging, or production."
  }
}

variable "vpc_cidr" {
  description = "CIDR block for the VPC"
  type        = string
  default     = "10.0.0.0/16"
}

variable "eks_cluster_version" {
  description = "Kubernetes version for the EKS cluster"
  type        = string
  default     = "1.29"
}

# ── Secrets (supply via TF_VAR_ env vars or a .tfvars file — never commit) ───

variable "razorpay_webhook_secret" {
  description = "Razorpay webhook HMAC secret"
  type        = string
  sensitive   = true
}

variable "openai_api_key" {
  description = "OpenAI API key (leave empty to use rule engine only)"
  type        = string
  sensitive   = true
  default     = ""
}

variable "audit_hmac_key" {
  description = "32-byte random key for audit ledger HMAC signatures"
  type        = string
  sensitive   = true
}

variable "whatsapp_access_token" {
  description = "Meta WhatsApp Business Cloud access token"
  type        = string
  sensitive   = true
  default     = ""
}

variable "twilio_auth_token" {
  description = "Twilio auth token for SMS dispatch"
  type        = string
  sensitive   = true
  default     = ""
}

variable "razorpay_key_id" {
  description = "Razorpay API Key ID for Payment Links"
  type        = string
  sensitive   = true
  default     = ""
}

variable "razorpay_key_secret" {
  description = "Razorpay API Key Secret for Payment Links"
  type        = string
  sensitive   = true
  default     = ""
}
