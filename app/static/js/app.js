/**
 * PlanWrite v2 - Client-side utilities
 * Most interactivity is handled by HTMX + Alpine.js
 * This file contains helper functions for complex operations
 */

// SSE streaming helper for generation endpoints
function streamGeneration(url, data, targetElement, onComplete) {
    const eventSource = new EventSource(url);
    const target = document.querySelector(targetElement);

    eventSource.onmessage = function(event) {
        const parsed = JSON.parse(event.data);

        switch (parsed.type) {
            case 'status':
                // Update status indicator
                const statusEl = target.querySelector('.status-message');
                if (statusEl) {
                    statusEl.textContent = parsed.message;
                }
                break;

            case 'token':
            case 'content':
                // Append content
                const contentEl = target.querySelector('.streaming-content');
                if (contentEl) {
                    contentEl.innerHTML += parsed.content;
                }
                break;

            case 'done':
                eventSource.close();
                if (onComplete) {
                    onComplete(parsed);
                }
                break;
        }
    };

    eventSource.onerror = function(error) {
        console.error('SSE Error:', error);
        eventSource.close();
        showToast('Generation failed. Please try again.', 'error');
    };

    return eventSource;
}

// Toast notification helper (also defined in base.html for inline use)
function showToast(message, type = 'info') {
    const container = document.getElementById('toast-container');
    if (!container) return;

    const colors = {
        'success': 'bg-green-500',
        'error': 'bg-red-500',
        'info': 'bg-blue-500',
        'warning': 'bg-yellow-500'
    };

    const toast = document.createElement('div');
    toast.className = `${colors[type]} text-white px-4 py-2 rounded-lg shadow-lg transform transition-all duration-300 translate-x-full`;
    toast.textContent = message;
    container.appendChild(toast);

    // Animate in
    setTimeout(() => toast.classList.remove('translate-x-full'), 10);

    // Remove after 3s
    setTimeout(() => {
        toast.classList.add('translate-x-full');
        setTimeout(() => toast.remove(), 300);
    }, 3000);
}

// Parse outline tokens from textarea
function parseOutlineTokens(text) {
    const lines = text.split('\n').filter(line => line.trim());
    const tokens = [];

    for (const line of lines) {
        const trimmed = line.trim();
        if (trimmed.startsWith('[') && trimmed.endsWith(']')) {
            tokens.push(trimmed);
        }
    }

    return tokens;
}

// Format word count with thousands separator
function formatWordCount(count) {
    return count.toLocaleString();
}

// Calculate reading time (avg 200 words per minute)
function calculateReadingTime(wordCount) {
    const minutes = Math.ceil(wordCount / 200);
    return `${minutes} min read`;
}

// Debounce helper for input handlers
function debounce(func, wait) {
    let timeout;
    return function executedFunction(...args) {
        const later = () => {
            clearTimeout(timeout);
            func(...args);
        };
        clearTimeout(timeout);
        timeout = setTimeout(later, wait);
    };
}

// Export as markdown file
function exportMarkdown(content, filename) {
    const blob = new Blob([content], { type: 'text/markdown' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename || 'article.md';
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
}

// Export as HTML file
function exportHTML(content, title, filename) {
    const html = `<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>${title || 'Article'}</title>
    <style>
        body { font-family: system-ui, sans-serif; max-width: 800px; margin: 0 auto; padding: 2rem; line-height: 1.6; }
        h1 { font-size: 2rem; margin-bottom: 1rem; }
        h2 { font-size: 1.5rem; margin-top: 2rem; }
        h3 { font-size: 1.25rem; margin-top: 1.5rem; }
        p { margin-bottom: 1rem; }
        a { color: #0ea5e9; }
    </style>
</head>
<body>
${content}
</body>
</html>`;

    const blob = new Blob([html], { type: 'text/html' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename || 'article.html';
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
}

// Initialize on page load
document.addEventListener('DOMContentLoaded', function() {
    console.log('PlanWrite v2 initialized');
});
