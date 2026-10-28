import pytest
from scripts.isolated_volume_snapshot import validate_projects


@pytest.mark.parametrize('projects',[['icp-workbench'],['icp-dify'],['icp-ragflow'],['icp-clean-dify','icp-workbench'],[],['icp-clean-dify; rm -rf /']])
def test_snapshot_refuses_live_projects_or_ambiguous_targets(projects):
    with pytest.raises(ValueError):validate_projects(projects)
