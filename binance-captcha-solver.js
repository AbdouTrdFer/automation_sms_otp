/**
 * BinanceBotCaptchaSolver
 * ─────────────────────────────────────────────────────────────────────
 * Auto-solves the Binance "Security Verification" slider CAPTCHA
 * using the noPecha image recognition API.
 *
 * This is a SEPARATE file — include it AFTER all existing scripts.
 * Do NOT modify any existing code. This runs independently.
 *
 * Usage:
 *   <script src="binance-captcha-solver.js"></script>
 *
 * Optional: pass your noPecha API key for higher rate limits
 *   <script>window.NOPECHA_API_KEY = "your-key";</script>
 *   <script src="binance-captcha-solver.js"></script>
 * ─────────────────────────────────────────────────────────────────────
 */

class BinanceBotCaptchaSolver {
  constructor(apiKey = null) {
    this.apiKey = apiKey || window.NOPECHA_API_KEY || null;
    this.baseUrl = "https://api.nopecha.com/v1";
    this.isMonitoring = false;
    this.isSolving = false;
    this.observer = null;

    if (this.apiKey) {
      console.log("📝 Using API key for noPecha");
    } else {
      console.log("📝 Using free tier (100 solves/day)");
    }
  }

  /* ─── Monitoring ─────────────────────────────────────────────── */

  startMonitoring() {
    if (this.isMonitoring) {
      console.log("⚠️ Already monitoring for CAPTCHA");
      return;
    }

    this.isMonitoring = true;
    console.log("🔍 Monitoring for CAPTCHA...");

    // MutationObserver for real-time DOM detection
    this.observer = new MutationObserver(() => this._checkCaptcha());

    this.observer.observe(document.body, {
      childList: true,
      subtree: true
    });

    // Fallback: periodic check (in case MutationObserver misses it)
    this._pollInterval = setInterval(() => this._checkCaptcha(), 1000);
  }

  stopMonitoring() {
    this.isMonitoring = false;
    if (this.observer) this.observer.disconnect();
    if (this._pollInterval) clearInterval(this._pollInterval);
  }

  _checkCaptcha() {
    if (this.isSolving) return;
    const modal = document.querySelector(".bs-modal");
    if (modal) {
      this.isSolving = true;
      console.log("✓ CAPTCHA modal detected");
      setTimeout(() => {
        this.solve().finally(() => {
          this.isSolving = false;
        });
      }, 500);
    }
  }

  /* ─── Utilities ──────────────────────────────────────────────── */

  sleep(ms) {
    return new Promise(r => setTimeout(r, ms));
  }

  async waitForElement(selector, timeoutMs = 3000) {
    const start = Date.now();
    while (Date.now() - start < timeoutMs) {
      const el = document.querySelector(selector);
      if (el && el.offsetParent !== null) return el;
      await new Promise(r => setTimeout(r, 100));
    }
    return null;
  }

  async urlToBase64(url) {
    const response = await fetch(url);
    if (!response.ok) throw new Error(`Failed to fetch image: ${response.status}`);
    const blob = await response.blob();
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => {
        const dataUrl = reader.result;
        const base64 = dataUrl.split(",")[1];
        resolve(base64);
      };
      reader.onerror = () => reject(new Error("FileReader error"));
      reader.readAsDataURL(blob);
    });
  }

  /* ─── STEP 1: Extract Puzzle Image ───────────────────────────── */

  async extractPuzzleImage() {
    console.log("📸 Extracting puzzle image...");

    try {
      // Wait for loading indicator to finish
      const tipMessage = document.querySelector(".bs-tipmessage-container");
      if (tipMessage) {
        let attempts = 0;
        while (attempts < 30 && window.getComputedStyle(tipMessage).display !== "none") {
          await this.sleep(100);
          attempts++;
        }
      }

      // Wait for the main image element to appear
      const mainImage = document.querySelector(".bs-main-image");
      if (!mainImage) throw new Error("Main image element not found");

      // Wait max 3 seconds for background-image to have a URL
      let bgImage = "";
      let attempts = 0;
      while (attempts < 30) {
        bgImage = window.getComputedStyle(mainImage).backgroundImage;
        if (bgImage && bgImage !== "none") break;
        await this.sleep(100);
        attempts++;
      }
      if (!bgImage || bgImage === "none") throw new Error("Background image not loaded");

      // Parse URL from: url("https://...") or url('https://...') or url(https://...)
      const urlMatch = bgImage.match(/url\(\s*["']?(.*?)["']?\s*\)/i);
      if (!urlMatch || !urlMatch[1]) throw new Error("Could not extract image URL from background-image");

      const imageUrl = urlMatch[1];
      const base64Image = await this.urlToBase64(imageUrl);
      if (!base64Image) throw new Error("Failed to convert image to Base64");

      const sizeKb = Math.round(base64Image.length / 1024);
      console.log(`✓ Image extracted (size: ${sizeKb}kb)`);
      return base64Image;

    } catch (error) {
      console.error("❌ Error extracting image:", error.message);
      throw error;
    }
  }

  /* ─── STEP 2: Submit to noPecha ──────────────────────────────── */

  async submitToNoPecha(base64Image) {
    console.log("📤 Submitting to noPecha...");

    try {
      const headers = { "Content-Type": "application/json" };
      if (this.apiKey) {
        headers["Authorization"] = `Basic ${this.apiKey}`;
      }

      const response = await fetch(`${this.baseUrl}/recognition/geetest`, {
        method: "POST",
        headers,
        body: JSON.stringify({
          task: "Slide the puzzle piece to complete the image",
          image_data: [base64Image]
        })
      });

      if (!response.ok) throw new Error(`API error: ${response.status} ${response.statusText}`);

      const data = await response.json();
      const jobId = data.data;
      if (!jobId) throw new Error("No job ID returned from noPecha");

      console.log(`✓ Job ID: ${jobId}`);
      return jobId;

    } catch (error) {
      console.error("❌ Error submitting to noPecha:", error.message);
      throw error;
    }
  }

  /* ─── STEP 3: Poll for Solution ──────────────────────────────── */

  async pollSolution(jobId, maxAttempts = 30, pollInterval = 1000) {
    console.log("⏳ Polling for solution...");

    const headers = {};
    if (this.apiKey) {
      headers["Authorization"] = `Basic ${this.apiKey}`;
    }

    try {
      for (let attempt = 1; attempt <= maxAttempts; attempt++) {
        const response = await fetch(
          `${this.baseUrl}/recognition/geetest?id=${jobId}`,
          { headers }
        );

        if (!response.ok) throw new Error(`API error: ${response.status}`);

        const data = await response.json();
        const solution = data.data;

        if (solution) {
          console.log(`✓ Solution received: x=${solution.x}%`);
          return solution;
        }

        if (attempt < maxAttempts) {
          console.log(`  Waiting... (${attempt}/${maxAttempts})`);
          await this.sleep(pollInterval);
        }
      }

      throw new Error(`Timeout: No solution after ${maxAttempts} attempts`);

    } catch (error) {
      console.error("❌ Error polling solution:", error.message);
      throw error;
    }
  }

  /* ─── STEP 4: Drag Slider ─────────────────────────────────────── */

  simulateDrag(element, startX, endX) {
    const rect = element.getBoundingClientRect();
    const startY = rect.top + rect.height / 2;

    // mousedown at start position
    element.dispatchEvent(new MouseEvent("mousedown", {
      clientX: startX,
      clientY: startY,
      view: window,
      bubbles: true,
      cancelable: true
    }));

    // mousemove in steps for realism
    const steps = 10;
    for (let i = 1; i <= steps; i++) {
      const x = Math.round(startX + (endX - startX) * (i / steps));
      document.dispatchEvent(new MouseEvent("mousemove", {
        clientX: x,
        clientY: Math.round(startY),
        view: window,
        bubbles: true,
        cancelable: true
      }));
    }

    // mouseup at target
    document.dispatchEvent(new MouseEvent("mouseup", {
      clientX: endX,
      clientY: Math.round(startY),
      view: window,
      bubbles: true,
      cancelable: true
    }));
  }

  async dragSlider(sliderPercentage) {
    console.log("🎯 Dragging slider...");

    try {
      const thumb = document.querySelector(".bs-slide-thumb");
      const container = document.querySelector(".verify-slider.bs-slide-container");

      if (!thumb || !container) {
        throw new Error("Slider elements not found (thumb or container)");
      }

      const containerRect = container.getBoundingClientRect();
      const containerWidth = containerRect.width;
      if (containerWidth <= 0) throw new Error("Container width is invalid");

      const targetDistance = (sliderPercentage / 100) * containerWidth;
      const startRect = thumb.getBoundingClientRect();
      const startX = startRect.left;
      const targetX = startX + targetDistance;

      console.log(`  Container: ${Math.round(containerWidth)}px, Target: ${Math.round(targetDistance)}px`);

      this.simulateDrag(thumb, startX, targetX);
      console.log("✓ Slider dragged");
      console.log("⏳ Verifying solution...");

      // Wait for server verification
      await this.sleep(2000);

    } catch (error) {
      console.error("❌ Error dragging slider:", error.message);
      throw error;
    }
  }

  /* ─── STEP 5: Verify Success ──────────────────────────────────── */

  async verifySolved() {
    try {
      await this.sleep(500);

      const successIcon = document.querySelector(".thumb-success");
      if (successIcon) {
        const display = window.getComputedStyle(successIcon).display;
        if (display !== "none") {
          console.log("✅ CAPTCHA solved!");
          return true;
        }
      }

      const failIcon = document.querySelector(".thumb-fail");
      if (failIcon) {
        const display = window.getComputedStyle(failIcon).display;
        if (display !== "none") {
          console.log("❌ CAPTCHA not solved");
          return false;
        }
      }

      console.log("❌ CAPTCHA not solved");
      return false;

    } catch (error) {
      console.error("❌ Error verifying solution:", error.message);
      return false;
    }
  }

  /* ─── Main Workflow ──────────────────────────────────────────── */

  async solve() {
    console.log("Starting CAPTCHA solve workflow...");

    try {
      const base64Image = await this.extractPuzzleImage();
      const jobId = await this.submitToNoPecha(base64Image);
      const solution = await this.pollSolution(jobId);
      await this.dragSlider(solution.x);
      await this.verifySolved();
    } catch (error) {
      console.error("❌ CAPTCHA solving failed:", error.message);
    }
  }
}

/* ─── Auto-instantiate ───────────────────────────────────────────
 * Creates a global window.binanceBotSolver for manual control.
 * To pass an API key: set window.NOPECHA_API_KEY before loading this script.
 * To start manually: call window.binanceBotSolver.startMonitoring()
 */
window.binanceBotSolver = new BinanceBotCaptchaSolver();
window.binanceBotSolver.startMonitoring();
