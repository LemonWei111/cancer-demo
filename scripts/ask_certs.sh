# 证书申请pipeline
cd acps_cli
uv run acps-cli --config acps-cli.toml auth login

uv run acps-cli --config acps-cli.toml cert eab fetch \
  --aic {AIC} \
  --output eab.json

uv run acps-cli --config acps-cli.toml cert issue \
  -a {AIC} \
  --eab-file eab.json \
  -u clientAuth \
  --key-path ./certs/agent-key.pem \
  --cert-path ./certs/agent-cert.pem \
  --trust-bundle-path ./certs/trust-bundle.pem
