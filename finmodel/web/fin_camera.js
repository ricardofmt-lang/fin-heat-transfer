(() => {
  const doc = window.parent.document;
  const captureUserCam = (gd, sc) => {
    const cam = sc && typeof sc.getCamera === 'function' ? sc.getCamera() : null;
    if (!cam || !cam.eye) return null;
    // Guard against Plotly's turntable gimbal collapse (~0, 0, r).
    if (Math.hypot(cam.eye.x || 0, cam.eye.y || 0) < 1e-4) return gd.__finUserCam || null;
    gd.__finUserCam = {
      eye: { ...cam.eye },
      center: { ...cam.center },
      up: { ...cam.up },
    };
    return gd.__finUserCam;
  };
  const lockSceneCamera = (keep) => {
    doc.querySelectorAll('.st-key-fin_3d .js-plotly-plot').forEach((gd) => {
      const hasMesh3d = Array.isArray(gd.data) && gd.data.some((tr) => tr.type === 'mesh3d');
      const scene = gd._fullLayout && gd._fullLayout.scene;
      const sc = scene && scene._scene;
      if (!hasMesh3d || !sc) return;
      if (!sc.__finCameraWrapped && sc.camera) {
        sc.__finCameraWrapped = true;
        const origInit = sc.initializeGLCamera.bind(sc);
        sc.initializeGLCamera = function() {
          if (this.__finKeepCam && this.camera) return;
          return origInit();
        };
        const origSetViewport = sc.setViewport.bind(sc);
        sc.setViewport = function(sceneLayout) {
          if (this.__finKeepCam && gd.__finUserCam && sceneLayout) {
            const proj = sceneLayout.camera && sceneLayout.camera.projection
              ? sceneLayout.camera.projection
              : { type: 'perspective' };
            sceneLayout = { ...sceneLayout, camera: { ...gd.__finUserCam, projection: proj } };
            if (gd.layout && gd.layout.scene) {
              gd.layout.scene.camera = { ...gd.__finUserCam };
            }
          }
          return origSetViewport(sceneLayout);
        };
      }
      if (keep) {
        const saved = captureUserCam(gd, sc);
        sc.__finKeepCam = true;
        if (saved && gd.layout && gd.layout.scene) {
          gd.layout.scene.camera = { ...saved };
        }
      } else {
        sc.__finKeepCam = false;
      }
    });
  };
  const syncCamera = (event) => {
    const target = event.target;
    if (!target.closest?.('.st-key-fin_3d')) return;
    if (target.closest('.updatemenu-button, .slider-container, .slider-group')) {
      lockSceneCamera(true);
    } else if (target.closest('.gl-container, .modebar-btn')) {
      lockSceneCamera(false);
    }
  };
  const trackCamera = (event) => {
    const plot = event.target.closest?.('.st-key-fin_3d .js-plotly-plot');
    if (plot && event.target.closest('.gl-container')) {
      setTimeout(() => {
        const scene = plot._fullLayout?.scene?._scene;
        if (scene && !scene.__finKeepCam) captureUserCam(plot, scene);
      }, 40);
    }
  };

  // Reload the handlers when a new result replaces the export iframe.
  const previousHandlers = doc.__finCameraHandlers;
  const handlers = {pointerdown: syncCamera, touchstart: syncCamera, pointerup: trackCamera, wheel: trackCamera};
  for (const [event, handler] of Object.entries(handlers)) {
    if (previousHandlers) doc.removeEventListener(event, previousHandlers[event], true);
    doc.addEventListener(event, handler, true);
  }
  doc.__finCameraHandlers = handlers;
})();
