from deploy.configure_pc_proxy import add_pc_proxy


def test_pc_proxy_is_sibling_preserves_existing_routes_and_is_idempotent():
    config = '''server {
    server_name drop.example.test;
    location ^~ /drop/ {
        proxy_pass http://127.0.0.1:8012;
        proxy_read_timeout 600s;
    }
    location /other/ { return 404; }
}
'''
    updated = add_pc_proxy(config)
    assert "location ^~ /drop-pc/ {" in updated
    assert "return 301 /drop-pc/;" in updated
    assert config.split("    location /other/")[0] in updated
    assert "location /other/ { return 404; }" in updated
    assert updated.count("proxy_pass http://127.0.0.1:8012;") == 2
    assert add_pc_proxy(updated) == updated
