import webview
import threading
import time
import sys

print('Creating splash window...')
html = """
<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>BirdEye - Starting</title>
    <style>
        body {
            margin: 0;
            padding: 0;
            background: #1a1a2e;
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
            height: 100vh;
            color: #eaeaea;
        }
        .container {
            text-align: center;
            max-width: 400px;
            padding: 2rem;
        }
        .logo {
            font-size: 3rem;
            margin-bottom: 1rem;
        }
        .title {
            font-size: 1.5rem;
            font-weight: 600;
            margin-bottom: 0.5rem;
            color: #fff;
        }
        .subtitle {
            color: #8892b0;
            margin-bottom: 2rem;
            font-size: 1rem;
        }
        .progress-container {
            width: 100%;
            height: 4px;
            background: #233554;
            border-radius: 2px;
            overflow: hidden;
            margin-bottom: 1.5rem;
        }
        .progress-bar {
            width: 0%;
            height: 100%;
            background: linear-gradient(90deg, #64ffda, #00b8a9);
            border-radius: 2px;
            transition: width 0.3s ease;
            animation: pulse 2s infinite;
        }
        @keyframes pulse {
            0% { opacity: 1; }
            50% { opacity: 0.6; }
            100% { opacity: 1; }
        }
        .status {
            color: #8892b0;
            font-size: 0.9rem;
            min-height: 1.5em;
        }
        .steps {
            margin-top: 2rem;
            text-align: left;
        }
        .step {
            display: flex;
            align-items: center;
            gap: 0.75rem;
            padding: 0.5rem 0;
            color: #8892b0;
            font-size: 0.9rem;
        }
        .step.completed {
            color: #64ffda;
        }
        .step.current {
            color: #fff;
        }
        .step-icon {
            width: 1.25rem;
            height: 1.25rem;
            flex-shrink: 0;
        }
        .spinner {
            width: 1.25rem;
            height: 1.25rem;
            border: 2px solid #233554;
            border-top-color: #64ffda;
            border-radius: 50%;
            animation: spin 1s linear infinite;
        }
        @keyframes spin {
            to { transform: rotate(360deg); }
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="logo">👁️</div>
        <h1 class="title">BirdEye</h1>
        <p class="subtitle">AI Video Attendance System</p>
        <div class="progress-container">
            <div class="progress-bar" id="progressBar"></div>
        </div>
        <div class="status" id="statusText">Starting BirdEye...</div>
        <div class="steps" id="steps">
            <div class="step" id="step-runtime">
                <span class="step-icon">⏳</span>
                <span>Checking local runtime</span>
            </div>
            <div class="step" id="step-db">
                <span class="step-icon">⏳</span>
                <span>Connecting to database</span>
            </div>
            <div class="step" id="step-inference">
                <span class="step-icon">⏳</span>
                <span>Initializing AI inference</span>
            </div>
            <div class="step" id="step-workers">
                <span class="step-icon">⏳</span>
                <span>Starting camera workers</span>
            </div>
            <div class="step" id="step-api">
                <span class="step-icon">⏳</span>
                <span>Starting API server</span>
            </div>
            <div class="step" id="step-ready">
                <span class="step-icon">⏳</span>
                <span>Ready to sign in</span>
            </div>
        </div>
    </div>
    <script>
        const steps = [
            {id: 'step-runtime', name: 'runtime'},
            {id: 'step-db', name: 'db'},
            {id: 'step-inference', name: 'inference'},
            {id: 'step-workers', name: 'workers'},
            {id: 'step-api', name: 'api'},
            {id: 'step-ready', name: 'ready'}
        ];
        let currentStep = 0;
        
        function updateProgress(percent, status, completedStep) {
            document.getElementById('progressBar').style.width = percent + '%';
            document.getElementById('statusText').textContent = status;
            
            if (completedStep !== undefined) {
                const stepEl = document.getElementById('step-' + completedStep);
                if (stepEl) {
                    stepEl.classList.remove('current');
                    stepEl.classList.add('completed');
                    stepEl.querySelector('.step-icon').textContent = '✓';
                }
            }
            
            if (currentStep < steps.length) {
                const nextStep = document.getElementById(steps[currentStep].id);
                if (nextStep) {
                    nextStep.classList.add('current');
                    if (nextStep.querySelector('.step-icon').textContent === '⏳') {
                        nextStep.querySelector('.step-icon').className = 'step-icon spinner';
                    }
                }
            }
        }
        
        window.pywebviewApi = {
            updateProgress: updateProgress
        };
    </script>
</body>
</html>
"""

import webview
import threading
import time
import sys

print('Creating splash window...')
w = webview.create_window('BirdEye - Starting', html=html, width=500, height=600, resizable=False, frameless=True, easy_drag=False, on_top=True)
print('Splash window created')

def close_it():
    time.sleep(2)
    print('Closing...')
    w.destroy()
    sys.exit(0)

t = threading.Thread(target=close_it)
t.daemon = True
t.start()

print('Starting webview...')
try:
    webview.start(debug=True)
except Exception as e:
    print(f'Error: {e}')
    import traceback
    traceback.print_exc()
print('Done')