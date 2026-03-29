function animateNumber(targetStr, duration) {
    // Type checking to ensure targetStr is a string
    if (typeof targetStr !== 'string') {
        throw new TypeError('Expected targetStr to be a string');
    }

    const start = parseFloat(targetStr.replace(/[^0-9.]/g, ''));
    const end = parseFloat(targetStr.replace(/[^0-9.]/g, ''));
    const startTime = performance.now();

    function update() {
        const currentTime = performance.now();
        const timeElapsed = currentTime - startTime;
        const progress = Math.min(timeElapsed / duration, 1);
        const animatedValue = start + (end - start) * progress;

        // Update your UI with animatedValue here

        if (progress < 1) {
            requestAnimationFrame(update);
        }
    }

    requestAnimationFrame(update);
}