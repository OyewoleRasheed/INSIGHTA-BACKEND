import pytest
from app import app

@pytest.fixture
def client():
    app.config['TESTING'] = True
    with app.test_client() as client:
        yield client

def test_health_check(client):
    """Ensure the health endpoint returns 200 OK."""
    response = client.get('/api/v1/health')
    assert response.status_code == 200
    assert response.json['status'] == 'ok'