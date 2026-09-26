"""Keep PPT Master dependencies out of the ordinary AIO sandbox image."""

import pytest


def test_separate_agent_host_image_preserves_aio_and_init_images(provisioner_module, monkeypatch):
    monkeypatch.delenv("PPT_MASTER_ENTERPRISE_RELAY_REQUIRED", raising=False)
    provisioner_module.PPT_MASTER_AGENT_HOST_ENABLED = True
    provisioner_module.SANDBOX_IMAGE = "registry.test/aio:stable"
    provisioner_module.PPT_MASTER_AGENT_HOST_IMAGE = "registry.test/ppt-agent:v660"

    pod = provisioner_module._build_pod("sandbox-1", "thread-1")

    assert [(container.name, container.image) for container in pod.spec.containers] == [
        ("sandbox", "registry.test/aio:stable"),
        ("ppt-master-agent-host", "registry.test/ppt-agent:v660"),
    ]
    assert all(container.image == "registry.test/aio:stable" for container in pod.spec.init_containers)


def test_agent_host_image_falls_back_to_aio_for_local_development(provisioner_module, monkeypatch):
    monkeypatch.delenv("PPT_MASTER_ENTERPRISE_RELAY_REQUIRED", raising=False)
    provisioner_module.PPT_MASTER_AGENT_HOST_ENABLED = True
    provisioner_module.SANDBOX_IMAGE = "registry.test/aio:stable"
    provisioner_module.PPT_MASTER_AGENT_HOST_IMAGE = ""

    pod = provisioner_module._build_pod("sandbox-1", "thread-1")

    assert [container.image for container in pod.spec.containers] == [
        "registry.test/aio:stable",
        "registry.test/aio:stable",
    ]


@pytest.mark.parametrize("host_image", ["", "registry.test/aio:stable"])
def test_enterprise_rejects_missing_or_shared_agent_host_image(provisioner_module, monkeypatch, host_image):
    monkeypatch.setenv("PPT_MASTER_ENTERPRISE_RELAY_REQUIRED", "true")
    provisioner_module.PPT_MASTER_AGENT_HOST_ENABLED = True
    provisioner_module.SANDBOX_IMAGE = "registry.test/aio:stable"
    provisioner_module.PPT_MASTER_AGENT_HOST_IMAGE = host_image

    with pytest.raises(RuntimeError, match="requires a distinct PPT_MASTER_AGENT_HOST_IMAGE"):
        provisioner_module._build_pod("sandbox-1", "thread-1")
