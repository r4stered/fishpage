# The one Actions secret CD needs: a Fly deploy token. Set machine-to-machine here so it is never
# pasted into the repo settings by hand. GHCR auth uses the workflow's built-in GITHUB_TOKEN, so no
# secret is needed for that.

resource "github_actions_secret" "fly_api_token" {
  repository      = var.github_repository
  secret_name     = "FLY_API_TOKEN"
  plaintext_value = var.fly_deploy_token
}
