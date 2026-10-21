with open('tests/test_process_safety.py', 'r') as f:
    content = f.read()

# Fix the TestBirdeyeAppSingleInstance class indentation
content = content.replace(
    '\n    class TestBirdeyeAppSingleInstance:\n    """Tests for BirdEyeApp single-instance protection."""\n\n    @patch',
    '\nclass TestBirdeyeAppSingleInstance:\n    """Tests for BirdEyeApp single-instance protection."""\n\n    @patch'
)

with open('tests/test_process_safety.py', 'w') as f:
    f.write(content)

print('Fixed')