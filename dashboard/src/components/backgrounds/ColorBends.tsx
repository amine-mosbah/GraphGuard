/**
 * ColorBends — the flowing colour-bend field React Bits runs behind its own hero.
 *
 * Ported from the React Bits registry entry (https://reactbits.dev/r/ColorBends-TS-TW.json,
 * "Vibrant color bends with smooth flowing animation") with one substitution: the registry
 * component depends on three.js, and this one does not.
 *
 * That substitution costs nothing visually, because three is not doing any 3-D work here.
 * The original builds an OrthographicCamera(-1, 1, 1, -1) over a PlaneGeometry(2, 2) and a
 * ShaderMaterial — that is a screen-filling quad with a fragment shader, which is fifty
 * lines of WebGL. Pulling in ~150 kB gzipped of scene graph, loaders and math to draw two
 * triangles is not a trade worth making on a landing page.
 *
 * THE FRAGMENT SHADER BELOW IS THE REGISTRY'S, CHARACTER FOR CHARACTER. It is the entire
 * visual, so it is deliberately not "cleaned up", reformatted or re-indented — the odd
 * indentation inside main() is upstream's and is left alone so the two can be diffed. The
 * context is WebGL1 for the same reason: the shader is already GLSL ES 1.00 (`varying`,
 * `gl_FragColor`), so running it on WebGL1 needs no translation and cannot drift. The
 * vertex shader is rewritten, because three supplies `position`/`uv` attributes from its
 * geometry and here they come from a buffer, but it computes the identical `vUv`: for a
 * quad spanning −1..1, uv is position * 0.5 + 0.5.
 *
 * Blending matches three's defaults for this material: the renderer is premultiplied-alpha
 * and the shader emits `col * a`, so the blend func is (ONE, ONE_MINUS_SRC_ALPHA).
 *
 * Added on top of the original: the animation clock freezes under
 * `prefers-reduced-motion: reduce`. docs/DESIGN.md §11.2 requires honouring that setting, and
 * the stylesheet's global rule only reaches CSS animations — it cannot stop a rAF loop.
 */

import { useEffect, useRef } from "react";
import type { CSSProperties } from "react";

type ColorBendsProps = {
  className?: string;
  style?: CSSProperties;
  rotation?: number;
  speed?: number;
  colors?: string[];
  transparent?: boolean;
  autoRotate?: number;
  scale?: number;
  frequency?: number;
  warpStrength?: number;
  mouseInfluence?: number;
  parallax?: number;
  noise?: number;
  iterations?: number;
  intensity?: number;
  bandWidth?: number;
};

const MAX_COLORS = 8 as const;

const frag = `
#define MAX_COLORS ${MAX_COLORS}
precision highp float;
uniform vec2 uCanvas;
uniform float uTime;
uniform float uSpeed;
uniform vec2 uRot;
uniform int uColorCount;
uniform vec3 uColors[MAX_COLORS];
uniform int uTransparent;
uniform float uScale;
uniform float uFrequency;
uniform float uWarpStrength;
uniform vec2 uPointer; // in NDC [-1,1]
uniform float uMouseInfluence;
uniform float uParallax;
uniform float uNoise;
uniform int uIterations;
uniform float uIntensity;
uniform float uBandWidth;
varying vec2 vUv;

void main() {
  float t = uTime * uSpeed;
  vec2 p = vUv * 2.0 - 1.0;
  p += uPointer * uParallax * 0.1;
  vec2 rp = vec2(p.x * uRot.x - p.y * uRot.y, p.x * uRot.y + p.y * uRot.x);
  vec2 q = vec2(rp.x * (uCanvas.x / uCanvas.y), rp.y);
  q /= max(uScale, 0.0001);
  q /= 0.5 + 0.2 * dot(q, q);
  q += 0.2 * cos(t) - 7.56;
  vec2 toward = (uPointer - rp);
  q += toward * uMouseInfluence * 0.2;

    for (int j = 0; j < 5; j++) {
      if (j >= uIterations - 1) break;
      vec2 rr = sin(1.5 * (q.yx * uFrequency) + 2.0 * cos(q * uFrequency));
      q += (rr - q) * 0.15;
    }

    vec3 col = vec3(0.0);
    float a = 1.0;

    if (uColorCount > 0) {
      vec2 s = q;
      vec3 sumCol = vec3(0.0);
      float cover = 0.0;
      for (int i = 0; i < MAX_COLORS; ++i) {
            if (i >= uColorCount) break;
            s -= 0.01;
            vec2 r = sin(1.5 * (s.yx * uFrequency) + 2.0 * cos(s * uFrequency));
            float m0 = length(r + sin(5.0 * r.y * uFrequency - 3.0 * t + float(i)) / 4.0);
            float kBelow = clamp(uWarpStrength, 0.0, 1.0);
            float kMix = pow(kBelow, 0.3); // strong response across 0..1
            float gain = 1.0 + max(uWarpStrength - 1.0, 0.0); // allow >1 to amplify displacement
            vec2 disp = (r - s) * kBelow;
            vec2 warped = s + disp * gain;
            float m1 = length(warped + sin(5.0 * warped.y * uFrequency - 3.0 * t + float(i)) / 4.0);
            float m = mix(m0, m1, kMix);
            float w = 1.0 - exp(-uBandWidth / exp(uBandWidth * m));
            sumCol += uColors[i] * w;
            cover = max(cover, w);
      }
      col = clamp(sumCol, 0.0, 1.0);
      a = uTransparent > 0 ? cover : 1.0;
    } else {
        vec2 s = q;
        for (int k = 0; k < 3; ++k) {
            s -= 0.01;
            vec2 r = sin(1.5 * (s.yx * uFrequency) + 2.0 * cos(s * uFrequency));
            float m0 = length(r + sin(5.0 * r.y * uFrequency - 3.0 * t + float(k)) / 4.0);
            float kBelow = clamp(uWarpStrength, 0.0, 1.0);
            float kMix = pow(kBelow, 0.3);
            float gain = 1.0 + max(uWarpStrength - 1.0, 0.0);
            vec2 disp = (r - s) * kBelow;
            vec2 warped = s + disp * gain;
            float m1 = length(warped + sin(5.0 * warped.y * uFrequency - 3.0 * t + float(k)) / 4.0);
            float m = mix(m0, m1, kMix);
            col[k] = 1.0 - exp(-uBandWidth / exp(uBandWidth * m));
        }
        a = uTransparent > 0 ? max(max(col.r, col.g), col.b) : 1.0;
    }

    col *= uIntensity;

    if (uNoise > 0.0001) {
      float n = fract(sin(dot(gl_FragCoord.xy + vec2(uTime), vec2(12.9898, 78.233))) * 43758.5453123);
      col += (n - 0.5) * uNoise;
      col = clamp(col, 0.0, 1.0);
    }

    vec3 rgb = (uTransparent > 0) ? col * a : col;
    gl_FragColor = vec4(rgb, a);
}
`;

const vert = `
attribute vec2 aPosition;
varying vec2 vUv;
void main() {
  vUv = aPosition * 0.5 + 0.5;
  gl_Position = vec4(aPosition, 0.0, 1.0);
}
`;

/** "#4F7CF0" / "#4cf" → [r, g, b] in 0..1, matching the registry's own parser. */
function toRgb(hex: string): [number, number, number] {
  const h = hex.replace("#", "").trim();
  const parts =
    h.length === 3
      ? [h[0] + h[0], h[1] + h[1], h[2] + h[2]]
      : [h.slice(0, 2), h.slice(2, 4), h.slice(4, 6)];
  return parts.map((part) => parseInt(part, 16) / 255) as [number, number, number];
}

function compile(gl: WebGLRenderingContext, type: number, source: string) {
  const shader = gl.createShader(type)!;
  gl.shaderSource(shader, source);
  gl.compileShader(shader);
  if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) {
    // Surfaced rather than swallowed: a shader that fails to compile draws nothing, and a
    // silently empty background is indistinguishable from one that is merely subtle.
    console.error(gl.getShaderInfoLog(shader));
    gl.deleteShader(shader);
    return null;
  }
  return shader;
}

export default function ColorBends({
  className = "",
  style,
  rotation = 90,
  speed = 0.2,
  colors = [],
  transparent = true,
  autoRotate = 0,
  scale = 1,
  frequency = 1,
  warpStrength = 1,
  mouseInfluence = 1,
  parallax = 0.5,
  noise = 0.15,
  iterations = 1,
  intensity = 1.5,
  bandWidth = 6,
}: ColorBendsProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);

  /*
   * Every animated prop is mirrored into a ref that the render loop reads. The loop is
   * created once and must never be torn down and rebuilt just because a colour changed —
   * that would drop the WebGL context and restart the clock, visibly jumping the field.
   */
  const propsRef = useRef({
    rotation,
    speed,
    colors,
    transparent,
    autoRotate,
    scale,
    frequency,
    warpStrength,
    mouseInfluence,
    parallax,
    noise,
    iterations,
    intensity,
    bandWidth,
  });
  propsRef.current = {
    rotation,
    speed,
    colors,
    transparent,
    autoRotate,
    scale,
    frequency,
    warpStrength,
    mouseInfluence,
    parallax,
    noise,
    iterations,
    intensity,
    bandWidth,
  };

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    const canvas = document.createElement("canvas");
    canvas.style.width = "100%";
    canvas.style.height = "100%";
    canvas.style.display = "block";

    const gl = (canvas.getContext("webgl", {
      alpha: true,
      antialias: false,
      premultipliedAlpha: true,
      powerPreference: "high-performance",
    }) ?? canvas.getContext("experimental-webgl")) as WebGLRenderingContext | null;
    if (!gl) return;

    container.appendChild(canvas);

    const vs = compile(gl, gl.VERTEX_SHADER, vert);
    const fs = compile(gl, gl.FRAGMENT_SHADER, frag);
    if (!vs || !fs) return;

    const program = gl.createProgram()!;
    gl.attachShader(program, vs);
    gl.attachShader(program, fs);
    gl.linkProgram(program);
    if (!gl.getProgramParameter(program, gl.LINK_STATUS)) {
      console.error(gl.getProgramInfoLog(program));
      return;
    }
    gl.useProgram(program);

    const buffer = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, buffer);
    gl.bufferData(
      gl.ARRAY_BUFFER,
      new Float32Array([-1, -1, 1, -1, -1, 1, -1, 1, 1, -1, 1, 1]),
      gl.STATIC_DRAW,
    );
    const aPosition = gl.getAttribLocation(program, "aPosition");
    gl.enableVertexAttribArray(aPosition);
    gl.vertexAttribPointer(aPosition, 2, gl.FLOAT, false, 0, 0);

    const loc = (name: string) => gl.getUniformLocation(program, name);
    const u = {
      canvas: loc("uCanvas"),
      time: loc("uTime"),
      speed: loc("uSpeed"),
      rot: loc("uRot"),
      colorCount: loc("uColorCount"),
      colors: loc("uColors[0]"),
      transparent: loc("uTransparent"),
      scale: loc("uScale"),
      frequency: loc("uFrequency"),
      warpStrength: loc("uWarpStrength"),
      pointer: loc("uPointer"),
      mouseInfluence: loc("uMouseInfluence"),
      parallax: loc("uParallax"),
      noise: loc("uNoise"),
      iterations: loc("uIterations"),
      intensity: loc("uIntensity"),
      bandWidth: loc("uBandWidth"),
    };

    // Premultiplied-alpha blending, matching three's WebGLRenderer default for the
    // registry's `premultipliedAlpha: true` material.
    gl.enable(gl.BLEND);
    gl.blendFunc(gl.ONE, gl.ONE_MINUS_SRC_ALPHA);
    gl.clearColor(0, 0, 0, 0);

    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const resize = () => {
      const w = container.clientWidth || 1;
      const h = container.clientHeight || 1;
      canvas.width = Math.round(w * dpr);
      canvas.height = Math.round(h * dpr);
      gl.viewport(0, 0, canvas.width, canvas.height);
      gl.uniform2f(u.canvas, w, h);
    };
    resize();

    let observer: ResizeObserver | null = null;
    if (typeof ResizeObserver !== "undefined") {
      observer = new ResizeObserver(resize);
      observer.observe(container);
    } else {
      window.addEventListener("resize", resize);
    }

    const pointerTarget = { x: 0, y: 0 };
    const pointerCurrent = { x: 0, y: 0 };
    /*
     * The registry listens on its own container. Here the background sits behind the hero
     * under `pointer-events: none` so it cannot swallow clicks on the buttons in front of
     * it — which also means it never receives a pointer event of its own. Listening on the
     * window and mapping into container coordinates keeps the parallax and mouse-influence
     * response identical while leaving the hero fully clickable.
     */
    const onPointerMove = (event: PointerEvent) => {
      const rect = container.getBoundingClientRect();
      pointerTarget.x = ((event.clientX - rect.left) / (rect.width || 1)) * 2 - 1;
      pointerTarget.y = -(((event.clientY - rect.top) / (rect.height || 1)) * 2 - 1);
    };
    window.addEventListener("pointermove", onPointerMove, { passive: true });

    const reduceMotion = window.matchMedia?.("(prefers-reduced-motion: reduce)");
    const colourBuffer = new Float32Array(MAX_COLORS * 3);

    let frame = 0;
    let last = performance.now();
    let elapsed = 0;

    const render = (now: number) => {
      const dt = Math.min((now - last) / 1000, 0.1);
      last = now;
      // Frozen rather than unmounted under reduced motion: the field still renders, it
      // just stops moving, so the page keeps its artwork.
      if (!reduceMotion?.matches) elapsed += dt;

      const p = propsRef.current;
      gl.uniform1f(u.time, elapsed);
      gl.uniform1f(u.speed, p.speed);
      gl.uniform1f(u.scale, p.scale);
      gl.uniform1f(u.frequency, p.frequency);
      gl.uniform1f(u.warpStrength, p.warpStrength);
      gl.uniform1f(u.mouseInfluence, p.mouseInfluence);
      gl.uniform1f(u.parallax, p.parallax);
      gl.uniform1f(u.noise, p.noise);
      gl.uniform1i(u.iterations, p.iterations);
      gl.uniform1f(u.intensity, p.intensity);
      gl.uniform1f(u.bandWidth, p.bandWidth);
      gl.uniform1i(u.transparent, p.transparent ? 1 : 0);

      const deg = (p.rotation % 360) + p.autoRotate * elapsed;
      const rad = (deg * Math.PI) / 180;
      gl.uniform2f(u.rot, Math.cos(rad), Math.sin(rad));

      const amt = Math.min(1, dt * 8);
      pointerCurrent.x += (pointerTarget.x - pointerCurrent.x) * amt;
      pointerCurrent.y += (pointerTarget.y - pointerCurrent.y) * amt;
      gl.uniform2f(u.pointer, pointerCurrent.x, pointerCurrent.y);

      const list = (p.colors ?? []).filter(Boolean).slice(0, MAX_COLORS);
      colourBuffer.fill(0);
      list.forEach((hex, index) => {
        const [r, g, b] = toRgb(hex);
        colourBuffer[index * 3] = r;
        colourBuffer[index * 3 + 1] = g;
        colourBuffer[index * 3 + 2] = b;
      });
      gl.uniform3fv(u.colors, colourBuffer);
      gl.uniform1i(u.colorCount, list.length);

      gl.clear(gl.COLOR_BUFFER_BIT);
      gl.drawArrays(gl.TRIANGLES, 0, 6);
      frame = requestAnimationFrame(render);
    };
    frame = requestAnimationFrame(render);

    return () => {
      cancelAnimationFrame(frame);
      if (observer) observer.disconnect();
      else window.removeEventListener("resize", resize);
      window.removeEventListener("pointermove", onPointerMove);
      gl.deleteBuffer(buffer);
      gl.deleteProgram(program);
      gl.deleteShader(vs);
      gl.deleteShader(fs);
      gl.getExtension("WEBGL_lose_context")?.loseContext();
      if (canvas.parentElement === container) container.removeChild(canvas);
    };
  }, []);

  return (
    <div
      ref={containerRef}
      className={`relative h-full w-full overflow-hidden ${className}`}
      style={style}
    />
  );
}
