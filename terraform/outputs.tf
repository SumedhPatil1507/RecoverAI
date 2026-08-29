###############################################################################
# RecoverAI Enterprise – Terraform Outputs
###############################################################################

output "eks_cluster_name" {
  description = "EKS cluster name"
  value       = module.eks.cluster_name
}

output "eks_cluster_endpoint" {
  description = "EKS cluster API endpoint"
  value       = module.eks.cluster_endpoint
  sensitive   = true
}

output "eks_cluster_certificate_authority_data" {
  description = "EKS cluster CA certificate (base64)"
  value       = module.eks.cluster_certificate_authority_data
  sensitive   = true
}

output "aurora_writer_endpoint" {
  description = "Aurora PostgreSQL writer endpoint"
  value       = module.aurora.cluster_endpoint
  sensitive   = true
}

output "aurora_reader_endpoint" {
  description = "Aurora PostgreSQL reader endpoint"
  value       = module.aurora.cluster_reader_endpoint
  sensitive   = true
}

output "aurora_port" {
  description = "Aurora PostgreSQL port"
  value       = module.aurora.cluster_port
}

output "redis_primary_endpoint" {
  description = "ElastiCache Redis primary endpoint"
  value       = aws_elasticache_replication_group.redis.primary_endpoint_address
  sensitive   = true
}

output "ecr_repository_url" {
  description = "ECR repository URL for Docker image pushes"
  value       = aws_ecr_repository.recoverai.repository_url
}

output "secrets_manager_arn" {
  description = "Secrets Manager secret ARN containing all app secrets"
  value       = aws_secretsmanager_secret.recoverai.arn
}

output "irsa_role_arn" {
  description = "IAM role ARN for RecoverAI pod service account (IRSA)"
  value       = module.irsa_recoverai.iam_role_arn
}

output "vpc_id" {
  description = "VPC ID"
  value       = module.vpc.vpc_id
}
