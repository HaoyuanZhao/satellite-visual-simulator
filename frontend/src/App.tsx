import { ChangeEvent, useEffect, useMemo, useRef, useState } from "react";
import * as Cesium from "cesium";
import "cesium/Build/Cesium/Widgets/widgets.css";
import { fetchFrames, fetchMetadata, fetchDatasets, ApiFrame, ApiIterationMeta, ApiRoutingPath, ApiSatellite, DatasetOption } from "./services/api";
import { advancePlaybackCursor, buildPlaybackTimeline, simulationTimeAtCursor } from "./services/playback";

interface InterpolatedSatellite extends ApiSatellite {
  latitude: number;
  longitude: number;
}

interface SatelliteTooltip {
  id: number;
  name: string;
  shell: number | null;
  orbit: number | null;
  latitude: number;
  longitude: number;
  altitude: number;
}

type ScreenPosition = {
  x: number;
  y: number;
};

type SelectedSet = Set<number>;
type OrbitsByShell = Map<number, SelectedSet>;
type CartesianCache = Map<number, Map<number, Cesium.Cartesian3>>;

const lerp = (a: number, b: number, t: number) => a + (b - a) * t;

const normalizeLongitude = (longitude: number): number => ((longitude + 540) % 360) - 180;

const interpolateLongitude = (fromLon: number, toLon: number, t: number): number => {
  const delta = normalizeLongitude(toLon - fromLon);
  return normalizeLongitude(fromLon + delta * t);
};

const uniqueSorted = (values: Array<number | null>): number[] =>
  Array.from(
    new Set(
      values
        .map((v) => (v === null || Number.isNaN(v) ? null : v))
        .filter((v): v is number => v !== null)
        .map((v) => Number(v)),
    ),
  ).sort((a, b) => a - b);

const formatFloat = (value: number, digits = 2): string =>
  Number.isFinite(value) ? value.toFixed(digits) : "N/A";

const toCartesian = (sat: ApiSatellite): Cesium.Cartesian3 | null => {
  if (!Number.isFinite(sat.sublong) || !Number.isFinite(sat.sublat) || !Number.isFinite(sat.altitude)) {
    return null;
  }

  const cart = Cesium.Cartesian3.fromDegrees(sat.sublong, sat.sublat, sat.altitude);
  return Number.isFinite(cart.x) && Number.isFinite(cart.y) && Number.isFinite(cart.z) ? cart : null;
};

const fromCartesian = (position: Cesium.Cartesian3): { latitude: number; longitude: number; altitude: number } => {
  const cartographic = Cesium.Cartographic.fromCartesian(position);
  return {
    latitude: Cesium.Math.toDegrees(cartographic.latitude),
    longitude: Cesium.Math.toDegrees(cartographic.longitude),
    altitude: cartographic.height,
  };
};

const interpolateFallbackLatLon = (
  s0: ApiSatellite,
  s1: ApiSatellite | undefined,
  t: number,
): Pick<InterpolatedSatellite, "latitude" | "longitude" | "altitude"> => ({
  latitude: lerp(s0.sublat, s1?.sublat ?? s0.sublat, t),
  longitude: interpolateLongitude(s0.sublong, s1?.sublong ?? s0.sublong, t),
  altitude: lerp(s0.altitude, s1?.altitude ?? s0.altitude, t),
});

const WORLD_CENTER = Cesium.Cartesian3.ZERO;
const MIN_RANGE = 5_000_000;
const MAX_RANGE = 120_000_000;
const INITIAL_HEADING = 0;
const INITIAL_PITCH = -45;
const INITIAL_ZOOM_STEP = 0.12;
const DRAG_ROTATE_SPEED_RAD = 0.003;
const INITIAL_FRAME_WINDOW = 12;
const FRAME_PRELOAD_RADIUS = 6;
const FAST_PLAYBACK_THRESHOLD = 100;
const FAST_PLAYBACK_BATCH_SIZE = 24;
const PLAYBACK_SPEEDS = [0.1, 0.25, 0.5, 1, 2, 5, 10, 100, 500, 1000, 5000, 10000];

function App() {
  const viewerRef = useRef<Cesium.Viewer | null>(null);
  const wheelHandlerRef = useRef<((event: WheelEvent) => void) | null>(null);
  const resizeHandlerRef = useRef<(() => void) | null>(null);
  const hoverHandlerRef = useRef<Cesium.ScreenSpaceEventHandler | null>(null);
  const dragStartRef = useRef<Cesium.Cartesian2 | null>(null);
  const isPlayingRef = useRef(false);
  const satLayerRef = useRef<Cesium.PointPrimitiveCollection | null>(null);
  const islLayerRef = useRef<Cesium.PolylineCollection | null>(null);
  const routeLayerRef = useRef<Cesium.PolylineCollection | null>(null);
  const gsLayerRef = useRef<Cesium.PointPrimitiveCollection | null>(null);
  const playbackAnimationRef = useRef<number | null>(null);
  const playCursorRef = useRef(0);
  const lastFrameTsRef = useRef(0);
  const datasetRequestIdRef = useRef(0);
  const pendingFrameRangeRef = useRef(new Set<string>());
  const frameByIterationRef = useRef<Map<number, ApiFrame>>(new Map());

  const [metadata, setMetadata] = useState<ApiIterationMeta[]>([]);
  const [stepSeconds, setStepSeconds] = useState<number | null>(null);
  const [datasetOptions, setDatasetOptions] = useState<DatasetOption[]>([]);
  const [selectedDataset, setSelectedDataset] = useState("");
  const [frameByIteration, setFrameByIteration] = useState<Map<number, ApiFrame>>(new Map());
  const [playCursor, setPlayCursor] = useState(0);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState("");
  const [showSatellites, setShowSatellites] = useState(true);
  const [showISL, setShowISL] = useState(true);
  const [showCrossShellOnly, setShowCrossShellOnly] = useState(false);
  const [showRouting, setShowRouting] = useState(true);
  const [showGs, setShowGs] = useState(false);
  const [selectedShells, setSelectedShells] = useState<SelectedSet>(new Set<number>());
  const [selectedOrbitsByShell, setSelectedOrbitsByShell] = useState<OrbitsByShell>(new Map());
  const [expandedShells, setExpandedShells] = useState<SelectedSet>(new Set());
  const [selectedRouteIndex, setSelectedRouteIndex] = useState(0);
  const [isPlaying, setIsPlaying] = useState(false);
  const [playbackSpeed, setPlaybackSpeed] = useState(1);
  const [hoverTooltip, setHoverTooltip] = useState<SatelliteTooltip | null>(null);
  const [hoverTooltipPos, setHoverTooltipPos] = useState<ScreenPosition | null>(null);

  const loadedFrames = useMemo(() => {
    return metadata
      .map((entry) => frameByIteration.get(entry.iteration))
      .filter((frame): frame is ApiFrame => frame !== undefined);
  }, [metadata, frameByIteration]);

  const maxFrameIndex = Math.max(metadata.length - 1, 0);
  const playbackTimeline = useMemo(() => buildPlaybackTimeline(metadata, stepSeconds), [metadata, stepSeconds]);
  const canPlay = playbackTimeline !== null && maxFrameIndex > 0;
  const needsFastPlaybackFrames = playbackSpeed >= FAST_PLAYBACK_THRESHOLD && loadedFrames.length < metadata.length;
  const framePos = Math.min(playCursor, maxFrameIndex);
  const lowerFrameIndex = Math.floor(framePos);
  const upperFrameIndex = Math.min(lowerFrameIndex + 1, maxFrameIndex);
  const interpT = framePos - lowerFrameIndex;
  const currentFrame = getFrameByMetadataIndex(lowerFrameIndex);
  const nextFrame = getFrameByMetadataIndex(upperFrameIndex);

  useEffect(() => {
    isPlayingRef.current = isPlaying;
  }, [isPlaying]);

  useEffect(() => {
    frameByIterationRef.current = frameByIteration;
  }, [frameByIteration]);

  const setFramesBatch = (incomingFrames: ApiFrame[]) => {
    if (incomingFrames.length === 0) return;
    setFrameByIteration((prev) => {
      const next = new Map(prev);
      let changed = false;
      for (const frame of incomingFrames) {
        if (!next.has(frame.iteration)) {
          next.set(frame.iteration, frame);
          changed = true;
        }
      }
      return changed ? next : prev;
    });
  };

  const loadFramesByMetadataRange = async (
    startIndex: number,
    endIndex: number,
    sourceMetadata: ApiIterationMeta[],
    datasetName: string | undefined,
    requestId: number,
    forceLoad = false,
  ): Promise<ApiFrame[]> => {
    if (sourceMetadata.length === 0 || startIndex > endIndex) {
      return [];
    }

    const startIdx = Math.max(0, Math.min(startIndex, sourceMetadata.length - 1));
    const endIdx = Math.max(startIdx, Math.min(endIndex, sourceMetadata.length - 1));
    const missingIterations: number[] = [];

    for (let i = startIdx; i <= endIdx; i++) {
      const iteration = sourceMetadata[i]?.iteration;
      if (iteration === undefined) {
        continue;
      }
      if (forceLoad || !frameByIterationRef.current.has(iteration)) {
        missingIterations.push(iteration);
      }
    }

    if (missingIterations.length === 0) {
      return [];
    }

    const rangeKey = `${requestId}:${datasetName || ""}:${startIdx}-${endIdx}`;
    if (pendingFrameRangeRef.current.has(rangeKey)) {
      return [];
    }

    pendingFrameRangeRef.current.add(rangeKey);
    try {
      const frames = await fetchFrames(
        missingIterations.at(0),
        missingIterations.at(-1),
        datasetName,
      );
      if (requestId !== datasetRequestIdRef.current) {
        return [];
      }
      setFramesBatch(frames);
      return frames;
    } finally {
      pendingFrameRangeRef.current.delete(rangeKey);
    }
  };

  function getFrameByMetadataIndex(metaIndex: number): ApiFrame | undefined {
    const iteration = metadata[metaIndex]?.iteration;
    if (iteration === undefined) return undefined;
    return frameByIteration.get(iteration);
  }

  useEffect(() => {
    const initCesium = async () => {
      try {
        const token = (import.meta.env.VITE_CESIUM_ION_TOKEN || "").toString();
        if (token) {
          Cesium.Ion.defaultAccessToken = token;
        }

        const viewer = viewerRef.current;
        if (!viewer) {
          const createEnglishBaseLayer = () =>
            new Cesium.ImageryLayer(
              new Cesium.UrlTemplateImageryProvider({
                url: "https://server.arcgisonline.com/ArcGIS/rest/services/World_Street_Map/MapServer/tile/{z}/{y}/{x}",
                credit: "ESRI World Street Map",
              }),
            );

          let baseLayer: Cesium.ImageryLayer | false = createEnglishBaseLayer();
          if (token) {
            try {
              baseLayer = createEnglishBaseLayer();
            } catch (providerErr) {
              setError(`Imagery provider failed, fallback to ESRI World Street Map: ${(providerErr as Error).message}`);
              baseLayer = createEnglishBaseLayer();
            }
          }

          const newViewer = new Cesium.Viewer("globeCanvas", {
            baseLayer,
            timeline: false,
            animation: false,
            baseLayerPicker: false,
            geocoder: false,
            homeButton: false,
            fullscreenButton: false,
            navigationHelpButton: false,
            infoBox: false,
            sceneModePicker: false,
            selectionIndicator: false,
            terrainShadows: Cesium.ShadowMode.DISABLED,
            shouldAnimate: true,
          });

          const camera = newViewer.camera;
          const controller = newViewer.scene.screenSpaceCameraController;
          controller.enableZoom = false;
          controller.enableTranslate = false;
          controller.enableRotate = false;
          controller.enableLook = false;
          controller.enableTilt = false;
          const canvas = newViewer.scene.canvas;

          const setCameraRange = (range: number, maintainOrientation = true) => {
            const safeRange = Cesium.Math.clamp(range, MIN_RANGE, MAX_RANGE);
            const currentPos = camera.positionWC;
            if (!currentPos) {
              return;
            }

            const direction = Cesium.Cartesian3.normalize(currentPos, new Cesium.Cartesian3());
            const destination = Cesium.Cartesian3.multiplyByScalar(direction, safeRange, new Cesium.Cartesian3());
            if (maintainOrientation && Number.isFinite(camera.heading)) {
              const heading = camera.heading;
              const pitch = camera.pitch;
              const roll = Number.isFinite(camera.roll) ? camera.roll : 0;
              if (Number.isFinite(pitch)) {
                camera.setView({
                  destination,
                  orientation: {
                    heading,
                    pitch,
                    roll,
                  },
                });
                return;
              }
            }

            camera.setView({
              destination,
              orientation: {
                heading: Cesium.Math.toRadians(INITIAL_HEADING),
                pitch: Cesium.Math.toRadians(INITIAL_PITCH),
                roll: 0,
              },
            });
          };

          const handleWheel = (event: WheelEvent) => {
            event.preventDefault();
            event.stopPropagation();

            const currentRange = Cesium.Cartesian3.magnitude(camera.positionWC);
            if (!Number.isFinite(currentRange) || currentRange <= 0) {
              return;
            }
            const direction = event.deltaY > 0 ? 1 : -1;
            const nextRange = currentRange * (1 + direction * INITIAL_ZOOM_STEP);
            setCameraRange(nextRange, true);
          };

          const updateHover = (position: Cesium.Cartesian2) => {
            const picked = newViewer.scene.pick(position);
            const pickedId = picked?.id as
              | {
                  type?: "satellite";
                  sat?: SatelliteTooltip;
                }
              | undefined;

            if (pickedId?.type === "satellite" && pickedId.sat) {
              setHoverTooltip(pickedId.sat);
              setHoverTooltipPos({
                x: position.x,
                y: position.y,
              });
            } else {
              setHoverTooltip(null);
              setHoverTooltipPos(null);
            }
          };

          const handleMouseMove = (movement: { endPosition: Cesium.Cartesian2 }) => {
            const endPosition = new Cesium.Cartesian2(movement.endPosition.x, movement.endPosition.y);
            const dragStart = dragStartRef.current;
            if (!dragStart) {
              if (isPlayingRef.current) {
                return;
              }
              updateHover(endPosition);
              return;
            }

            const deltaX = endPosition.x - dragStart.x;
            const deltaY = endPosition.y - dragStart.y;
            dragStartRef.current = endPosition;

            if (deltaX === 0 && deltaY === 0) {
              return;
            }

            if (deltaX !== 0) {
              camera.rotateRight(-deltaX * DRAG_ROTATE_SPEED_RAD);
            }
            if (deltaY !== 0) {
              camera.rotateUp(-deltaY * DRAG_ROTATE_SPEED_RAD);
            }
          };

          const handleLeftDown = (movement: { position: Cesium.Cartesian2 }) => {
            dragStartRef.current = new Cesium.Cartesian2(movement.position.x, movement.position.y);
            setHoverTooltip(null);
            setHoverTooltipPos(null);
          };

          const handlePointerUp = () => {
            dragStartRef.current = null;
            setHoverTooltip(null);
            setHoverTooltipPos(null);
          };

          newViewer.scene.globe.enableLighting = false;
          newViewer.scene.backgroundColor = Cesium.Color.fromCssColorString("#050713");
          newViewer.scene.globe.baseColor = Cesium.Color.fromCssColorString("#10264a");
          if (newViewer.scene.moon) newViewer.scene.moon.show = false;
          if (newViewer.scene.skyBox) newViewer.scene.skyBox.show = false;
          if (newViewer.scene.sun) newViewer.scene.sun.show = false;
          newViewer.scene.fog.enabled = false;

          const hostCanvas = newViewer.scene.canvas;
          hostCanvas.style.width = "100%";
          hostCanvas.style.height = "100%";
          hostCanvas.style.display = "block";
          const initialRange = 23000000;
          camera.lookAt(
            WORLD_CENTER,
            new Cesium.HeadingPitchRange(
              Cesium.Math.toRadians(INITIAL_HEADING),
              Cesium.Math.toRadians(INITIAL_PITCH),
              initialRange,
            ),
          );

          viewerRef.current = newViewer;

          wheelHandlerRef.current = handleWheel;
          hoverHandlerRef.current = new Cesium.ScreenSpaceEventHandler(canvas);
          hoverHandlerRef.current.setInputAction(handleMouseMove, Cesium.ScreenSpaceEventType.MOUSE_MOVE);
          hoverHandlerRef.current.setInputAction(handleLeftDown, Cesium.ScreenSpaceEventType.LEFT_DOWN);
          hoverHandlerRef.current.setInputAction(handlePointerUp, Cesium.ScreenSpaceEventType.LEFT_UP);
          resizeHandlerRef.current = () => newViewer.resize();
          canvas.addEventListener("wheel", handleWheel, { passive: false });
          window.addEventListener("resize", resizeHandlerRef.current);
        }
      } catch (err) {
        setError(`Cesium init failed: ${(err as Error).message}`);
      }
    };

    initCesium();
    return () => {
      const viewer = viewerRef.current;
      if (viewer && wheelHandlerRef.current) {
        viewer.scene.canvas.removeEventListener("wheel", wheelHandlerRef.current);
      }
      if (hoverHandlerRef.current) {
        hoverHandlerRef.current.destroy();
        hoverHandlerRef.current = null;
      }
      if (resizeHandlerRef.current) {
        window.removeEventListener("resize", resizeHandlerRef.current);
      }
      wheelHandlerRef.current = null;
      resizeHandlerRef.current = null;
      setHoverTooltip(null);
      setHoverTooltipPos(null);
      if (viewer) {
        viewer.scene.screenSpaceCameraController.enableZoom = true;
        viewer.scene.screenSpaceCameraController.enableTranslate = true;
        viewer.scene.screenSpaceCameraController.enableRotate = true;
        viewer.scene.screenSpaceCameraController.enableLook = true;
        viewer.scene.screenSpaceCameraController.enableTilt = true;
      }
      if (playbackAnimationRef.current) {
        cancelAnimationFrame(playbackAnimationRef.current);
        playbackAnimationRef.current = null;
      }
      if (viewerRef.current) {
        viewerRef.current.destroy();
        viewerRef.current = null;
      }
    };
  }, []);

  const loadDatasetData = async (datasetName?: string) => {
    const requestId = ++datasetRequestIdRef.current;
    try {
      setIsLoading(true);
      setError("");
      setMetadata([]);
      setStepSeconds(null);
      setIsPlaying(false);
      isPlayingRef.current = false;
      setFrameByIteration(new Map());
      setHoverTooltip(null);
      setHoverTooltipPos(null);
      setSelectedShells(new Set());
      setSelectedOrbitsByShell(new Map());
      setSelectedRouteIndex(0);
      setPlayCursor(0);
      playCursorRef.current = 0;

      const meta = await fetchMetadata(datasetName);
      if (requestId !== datasetRequestIdRef.current) {
        return;
      }
      setMetadata(meta.iterations);
      setStepSeconds(meta.step_seconds ?? null);

      const initialWindowEnd = Math.min(INITIAL_FRAME_WINDOW - 1, meta.iterations.length - 1);
      const framesData = await loadFramesByMetadataRange(
        0,
        initialWindowEnd,
        meta.iterations,
        datasetName,
        requestId,
        true,
      );
      if (requestId !== datasetRequestIdRef.current) {
        return;
      }

      if (framesData.length > 0) {
        const shellSet = uniqueSorted(
          framesData.flatMap((frame) => frame.satellites.map((sat) => sat.shell)),
        );
        const shellOrbitMap = new Map<number, Set<number>>();
        for (const frame of framesData) {
          for (const sat of frame.satellites) {
            if (sat.shell === null) {
              continue;
            }
            if (!shellOrbitMap.has(sat.shell)) {
              shellOrbitMap.set(sat.shell, new Set());
            }
            if (sat.orbit !== null) {
              shellOrbitMap.get(sat.shell)?.add(sat.orbit);
            }
          }
        }
        const normalizedShellOrbits = new Map<number, Set<number>>();
        for (const [shell, orbits] of shellOrbitMap) {
          normalizedShellOrbits.set(shell, new Set(orbits));
        }
        setSelectedShells(new Set(shellSet));
        setSelectedOrbitsByShell(normalizedShellOrbits);
        setExpandedShells(new Set());
      }
    } catch (e) {
      setError(`Load failed: ${(e as Error).message}`);
    } finally {
      if (requestId === datasetRequestIdRef.current) {
        setIsLoading(false);
      }
    }
  };

  const switchDataset = async (datasetName: string) => {
    setSelectedDataset(datasetName);
    await loadDatasetData(datasetName);
  };

  useEffect(() => {
    const init = async () => {
      try {
        const datasets = await fetchDatasets();
        setDatasetOptions(datasets);
        if (datasets.length > 0) {
          const initialDataset = datasets[0].name;
          setSelectedDataset(initialDataset);
          await loadDatasetData(initialDataset);
          return;
        }
      } catch (e) {
        setDatasetOptions([]);
      }

      setSelectedDataset("");
      await loadDatasetData();
    };

    void init();
  }, []);

  useEffect(() => {
    if (isLoading || !canPlay || getFrameByMetadataIndex(0) === undefined) {
      return;
    }
    playCursorRef.current = 0;
    setPlayCursor(0);
    setIsPlaying(true);
  }, [isLoading, playbackTimeline]);

  useEffect(() => {
    if (isLoading || metadata.length === 0) {
      return;
    }

    const targetIndex = Math.min(Math.max(Math.floor(playCursor), 0), metadata.length - 1);
    const startIndex = Math.max(0, targetIndex - FRAME_PRELOAD_RADIUS);
    const endIndex = Math.min(metadata.length - 1, targetIndex + FRAME_PRELOAD_RADIUS);
    void loadFramesByMetadataRange(
      startIndex,
      endIndex,
      metadata,
      selectedDataset,
      datasetRequestIdRef.current,
    ).catch(() => {
      // ignore prefetch failures so UI remains responsive while dragging/playback continues
    });
  }, [isLoading, metadata.length, metadata, selectedDataset, playCursor]);

  useEffect(() => {
    if (isLoading || playbackSpeed < FAST_PLAYBACK_THRESHOLD || metadata.length === 0) return;
    let canceled = false;
    const requestId = datasetRequestIdRef.current;
    const preload = async () => {
      for (let start = 0; start < metadata.length; start += FAST_PLAYBACK_BATCH_SIZE) {
        if (canceled || requestId !== datasetRequestIdRef.current) return;
        await loadFramesByMetadataRange(
          start, Math.min(start + FAST_PLAYBACK_BATCH_SIZE - 1, metadata.length - 1),
          metadata, selectedDataset, requestId,
        );
      }
    };
    void preload().catch((e) => {
      if (!canceled && requestId === datasetRequestIdRef.current) {
        setError(`Playback frames could not be loaded: ${(e as Error).message}`);
        setIsPlaying(false);
      }
    });
    return () => { canceled = true; };
  }, [isLoading, playbackSpeed, metadata, selectedDataset]);

  useEffect(() => {
    if (!currentFrame) return;
    const routeCount = currentFrame.routing_paths.length;
    if (routeCount === 0) {
      setSelectedRouteIndex(0);
      return;
    }
    if (selectedRouteIndex > routeCount - 1) {
      setSelectedRouteIndex(0);
    }
  }, [currentFrame, selectedRouteIndex]);

  const allShells = useMemo(() => {
    const values = loadedFrames.flatMap((frame) => frame.satellites.map((s) => s.shell));
    return uniqueSorted(values);
  }, [loadedFrames]);

  const allOrbitsByShell = useMemo(() => {
    const result = new Map<number, Set<number>>();
    for (const frame of loadedFrames) {
      for (const sat of frame.satellites) {
        if (sat.shell === null || sat.orbit === null) {
          continue;
        }
        const current = result.get(sat.shell);
        if (current) {
          current.add(sat.orbit);
        } else {
          result.set(sat.shell, new Set([sat.orbit]));
        }
      }
    }
    return result;
  }, [loadedFrames]);

  const cloneOrbitSelection = (source: OrbitsByShell): OrbitsByShell =>
    new Map([...source].map(([shell, orbits]) => [shell, new Set(orbits)]));

  const toggleShellExpand = (shell: number) => {
    const next = new Set(expandedShells);
    if (next.has(shell)) {
      next.delete(shell);
    } else {
      next.add(shell);
    }
    setExpandedShells(next);
  };

  const getOrbitUniverse = (shell: number): SelectedSet => allOrbitsByShell.get(shell) ?? new Set();

  const isSatelliteVisibleByFilter = (sat: ApiSatellite): boolean => {
    if (sat.shell !== null && selectedShells.size > 0 && !selectedShells.has(sat.shell)) {
      return false;
    }

    if (sat.shell === null || sat.orbit === null) {
      return true;
    }

    const shellOrbits = allOrbitsByShell.get(sat.shell);
    if (!shellOrbits || shellOrbits.size === 0) {
      return true;
    }

    const selectedOrbits = selectedOrbitsByShell.get(sat.shell);
    if (!selectedOrbits || selectedOrbits.size === 0) {
      return true;
    }
    return selectedOrbits.has(sat.orbit);
  };

  const frameMap = useMemo(() => {
    const map = new Map<number, Map<number, ApiSatellite>>();
    for (const frame of loadedFrames) {
      const entry = new Map<number, ApiSatellite>();
      for (const sat of frame.satellites) {
        if (sat.id !== null) {
          entry.set(sat.id, sat);
        }
      }
      map.set(frame.iteration, entry);
    }
    return map;
  }, [loadedFrames]);

  const frameCartesianMap = useMemo<CartesianCache>(() => {
    const cache: CartesianCache = new Map();
    for (const frame of loadedFrames) {
      const frameMapCache = new Map<number, Cesium.Cartesian3>();
      for (const sat of frame.satellites) {
        const position = toCartesian(sat);
        if (position) {
          frameMapCache.set(sat.id, position);
        }
      }
      cache.set(frame.iteration, frameMapCache);
    }
    return cache;
  }, [loadedFrames]);

  const interpolateSatellites = (
    satA: Map<number, ApiSatellite>,
    satB: Map<number, ApiSatellite> | undefined,
    t: number,
    cartA: Map<number, Cesium.Cartesian3> | undefined,
    cartB: Map<number, Cesium.Cartesian3> | undefined,
  ): InterpolatedSatellite[] => {
    const output: InterpolatedSatellite[] = [];
    for (const satId of satA.keys()) {
      const s0 = satA.get(satId);
      if (!s0 || !isSatelliteVisibleByFilter(s0)) {
        continue;
      }

      const s1 = satB?.get(satId);
      const p0 = cartA?.get(satId);
      const p1 = cartB?.get(satId);

      let lat = s0.sublat;
      let lon = s0.sublong;
      let alt = s0.altitude;

      if (p0 && p1 && Number.isFinite(p0.x) && Number.isFinite(p0.y) && Number.isFinite(p0.z) && Number.isFinite(p1.x) && Number.isFinite(p1.y) && Number.isFinite(p1.z)) {
        const interpolatedPos = Cesium.Cartesian3.lerp(p0, p1, t, new Cesium.Cartesian3());
        if (Number.isFinite(interpolatedPos.x) && Number.isFinite(interpolatedPos.y) && Number.isFinite(interpolatedPos.z)) {
          const geo = fromCartesian(interpolatedPos);
          if (Number.isFinite(geo.latitude) && Number.isFinite(geo.longitude) && Number.isFinite(geo.altitude)) {
            lat = geo.latitude;
            lon = geo.longitude;
            alt = geo.altitude;
          } else {
            ({ latitude: lat, longitude: lon, altitude: alt } = interpolateFallbackLatLon(s0, s1, t));
          }
        } else {
          ({ latitude: lat, longitude: lon, altitude: alt } = interpolateFallbackLatLon(s0, s1, t));
        }
      } else {
        ({ latitude: lat, longitude: lon, altitude: alt } = interpolateFallbackLatLon(s0, s1, t));
      }

      output.push({ ...s0, latitude: lat, longitude: lon, sublat: lat, sublong: lon, altitude: alt });
    }
    return output;
  };

  const allRoutes = currentFrame?.routing_paths ?? [];
  const currentRoute = allRoutes[selectedRouteIndex];
  const currentFrameSatMap = currentFrame ? frameMap.get(currentFrame.iteration) : undefined;
  const routeHiddenByShellFilter = useMemo(() => {
    if (!currentRoute || !currentFrameSatMap || selectedShells.size === 0) {
      return false;
    }

    const routeSatIds = [
      currentRoute.src_sat_id,
      ...currentRoute.path,
      currentRoute.dst_sat_id,
    ].filter((value): value is number => value !== null && Number.isFinite(value));

    const isRouteSatVisible = (satId: number): boolean => {
      const sat = currentFrameSatMap.get(Number(satId));
      if (!sat) {
        return false;
      }
      if (sat.shell === null) {
        return true;
      }
      return isSatelliteVisibleByFilter(sat);
    };

    return routeSatIds.some((satId) => !isRouteSatVisible(Number(satId)));
  }, [currentRoute, currentFrameSatMap, selectedShells, selectedOrbitsByShell, allOrbitsByShell]);
  const routeLabel = allRoutes[selectedRouteIndex]
    ? `#${selectedRouteIndex + 1} ${allRoutes[selectedRouteIndex].src_sat_id ?? "src"} -> ${allRoutes[selectedRouteIndex].dst_sat_id ?? "dst"} (${allRoutes[selectedRouteIndex].task_state ?? "NA"})`
    : "No routing path";

  const renderScene = () => {
    const viewer = viewerRef.current;
    if (!viewer || !currentFrame || !nextFrame) return;

    const baseA = new Map<number, ApiSatellite>();
    for (const sat of currentFrame.satellites) {
      baseA.set(sat.id, sat);
    }
    const baseB = new Map<number, ApiSatellite>();
    for (const sat of nextFrame.satellites) {
      baseB.set(sat.id, sat);
    }

    const clearCollection = (collection: Cesium.PointPrimitiveCollection | Cesium.PolylineCollection | null) => {
      if (!collection || !viewer) return;
      viewer.scene.primitives.remove(collection);
    };

    clearCollection(satLayerRef.current);
    clearCollection(islLayerRef.current);
    clearCollection(routeLayerRef.current);
    clearCollection(gsLayerRef.current);
    satLayerRef.current = null;
    islLayerRef.current = null;
    routeLayerRef.current = null;
    gsLayerRef.current = null;

    const interpolated = interpolateSatellites(
      baseA,
      baseB,
      interpT,
      currentFrame ? frameCartesianMap.get(currentFrame.iteration) : undefined,
      nextFrame ? frameCartesianMap.get(nextFrame.iteration) : undefined,
    );
    const satLookup = new Map(interpolated.map((sat) => [sat.id, sat]));

    if (showSatellites) {
      const pointCollection = new Cesium.PointPrimitiveCollection();
      for (const sat of interpolated) {
        const pos = Cesium.Cartesian3.fromDegrees(sat.longitude, sat.latitude, sat.altitude);
        pointCollection.add({
          id: {
            type: "satellite",
            sat: {
              id: sat.id,
              name: sat.name || `sat-${sat.id}`,
              shell: sat.shell,
              orbit: sat.orbit,
              latitude: sat.latitude,
              longitude: sat.longitude,
              altitude: sat.altitude / 1000,
            },
          },
          position: pos,
          color: Cesium.Color.fromCssColorString("#3ec4ff"),
          pixelSize: 4,
          outlineColor: Cesium.Color.BLACK,
          outlineWidth: 1,
        });
      }
      viewer.scene.primitives.add(pointCollection);
      satLayerRef.current = pointCollection;
    }

    if (showGs && currentFrame.gs_stations.length > 0) {
      const gsCollection = new Cesium.PointPrimitiveCollection();
      for (const gs of currentFrame.gs_stations) {
        gsCollection.add({
          position: Cesium.Cartesian3.fromDegrees(gs.lon, gs.lat, 0),
          color: Cesium.Color.fromCssColorString("#ff4f4f"),
          pixelSize: 6,
          outlineColor: Cesium.Color.WHITE,
          outlineWidth: 1,
        });
      }
      viewer.scene.primitives.add(gsCollection);
      gsLayerRef.current = gsCollection;
    }

        if (showISL) {
      const lineCollection = new Cesium.PolylineCollection();
      for (const edge of currentFrame.isl_edges) {
        const srcSat = satLookup.get(edge.src_sat_id);
        const dstSat = satLookup.get(edge.dst_sat_id);
        if (!srcSat || !dstSat) continue;

        if (!isSatelliteVisibleByFilter(srcSat) || !isSatelliteVisibleByFilter(dstSat)) {
          continue;
        }

        if (showCrossShellOnly && !edge.cross_shell) continue;

        const color = edge.cross_shell
          ? Cesium.Color.fromCssColorString("#7492bf")
          : Cesium.Color.fromCssColorString("#9bd2ff");
        const width = edge.cross_shell ? 2.2 : 1.8;
        lineCollection.add({
          positions: [Cesium.Cartesian3.fromDegrees(srcSat.longitude, srcSat.latitude, srcSat.altitude), Cesium.Cartesian3.fromDegrees(dstSat.longitude, dstSat.latitude, dstSat.altitude)],
          width,
          material: Cesium.Material.fromType("Color", { color }),
        });
      }
      viewer.scene.primitives.add(lineCollection);
      islLayerRef.current = lineCollection;
    }

    if (showRouting && currentFrame.routing_paths.length > 0 && currentRoute && !routeHiddenByShellFilter) {
      const route: ApiRoutingPath = currentFrame.routing_paths[selectedRouteIndex];
      const points = [Cesium.Cartesian3.fromDegrees(route.src_lon, route.src_lat, 0)];

      if (route.path.length > 0) {
        for (const satId of route.path) {
          const sat = satLookup.get(Number(satId));
          if (!sat) continue;
          points.push(Cesium.Cartesian3.fromDegrees(sat.longitude, sat.latitude, sat.altitude));
        }
      } else {
        points.push(Cesium.Cartesian3.fromDegrees(route.dst_lon, route.dst_lat, 0));
      }
      points.push(Cesium.Cartesian3.fromDegrees(route.dst_lon, route.dst_lat, 0));

      if (points.length >= 2) {
        const routeCollection = new Cesium.PolylineCollection();
        routeCollection.add({
          positions: points,
          width: 4.0,
          material: Cesium.Material.fromType("Color", {
            color: Cesium.Color.fromCssColorString("#ffd23f"),
          }),
          show: true,
        });
        viewer.scene.primitives.add(routeCollection);
        routeLayerRef.current = routeCollection;
      }
    }
  };

  useEffect(() => {
    if (!viewerRef.current || metadata.length === 0 || currentFrame === undefined || nextFrame === undefined) return;
    renderScene();
  }, [
    playCursor,
    loadedFrames,
    showSatellites,
    showISL,
    showRouting,
    showGs,
    showCrossShellOnly,
    selectedShells,
    selectedOrbitsByShell,
    allOrbitsByShell,
    selectedRouteIndex,
    routeHiddenByShellFilter,
    currentFrame,
    nextFrame,
    interpT,
  ]);

  useEffect(() => {
    if (!isPlaying || !canPlay || playbackTimeline === null || needsFastPlaybackFrames) {
      return;
    }

    const tick = (ts: number) => {
      if (!lastFrameTsRef.current) {
        lastFrameTsRef.current = ts;
      }
      const simulatedSeconds = ((ts - lastFrameTsRef.current) / 1000) * playbackSpeed;
      lastFrameTsRef.current = ts;
      const next = advancePlaybackCursor(playCursorRef.current, simulatedSeconds, playbackTimeline);
      playCursorRef.current = next;
      setPlayCursor(next);
      playbackAnimationRef.current = requestAnimationFrame(tick);
    };

    playbackAnimationRef.current = requestAnimationFrame(tick);
    return () => {
      if (playbackAnimationRef.current !== null) {
        cancelAnimationFrame(playbackAnimationRef.current);
        playbackAnimationRef.current = null;
      }
      lastFrameTsRef.current = 0;
    };
  }, [isPlaying, playbackSpeed, playbackTimeline, needsFastPlaybackFrames]);

  useEffect(() => {
    playCursorRef.current = playCursor;
  }, [playCursor]);

  const iterationLabel = useMemo(() => {
    if (metadata.length === 0) return "iter 0";
    const lower = currentFrame;
    const upper = nextFrame;
    if (!lower) {
      return "iter N/A";
    }
    const label = interpT === 0 || !upper ? lower.iteration : formatFloat(lower.iteration * (1 - interpT) + upper.iteration * interpT, 2);
    return `iter ${label}`;
  }, [interpT, currentFrame, nextFrame]);

  const selectedTimeMs = simulationTimeAtCursor(metadata, framePos, playbackTimeline);
  const selectedTimeIso = selectedTimeMs === null ? undefined : new Date(selectedTimeMs).toISOString();
  const selectedTime = selectedTimeIso?.slice(0, 19).replace("T", " ") ?? "N/A";

  const toggleShell = (shell: number) => {
    const next = new Set(selectedShells);
    if (next.has(shell)) {
      next.delete(shell);
    } else {
      next.add(shell);
    }
    const nextOrbitByShell = cloneOrbitSelection(selectedOrbitsByShell);
    if (next.has(shell) && !nextOrbitByShell.has(shell)) {
      nextOrbitByShell.set(shell, new Set());
    }
    setSelectedShells(next);
    setSelectedOrbitsByShell(nextOrbitByShell);
  };

  const toggleOrbit = (shell: number, orbit: number) => {
    const shellOrbitUniverse = allOrbitsByShell.get(shell);
    if (!shellOrbitUniverse || shellOrbitUniverse.size === 0) {
      return;
    }

    const current = selectedOrbitsByShell.get(shell);
    const next = new Set(current && current.size > 0 ? current : shellOrbitUniverse);
    if (next.has(orbit)) {
      next.delete(orbit);
    } else {
      next.add(orbit);
    }
    const normalizedNext = next.size === shellOrbitUniverse.size ? new Set<number>() : next;
    const nextOrbitByShell = cloneOrbitSelection(selectedOrbitsByShell);
    nextOrbitByShell.set(shell, normalizedNext);
    setSelectedOrbitsByShell(nextOrbitByShell);
  };

  const onPlayToggle = () => {
    setIsPlaying((previouslyPlaying) => {
      if (previouslyPlaying) {
        setHoverTooltip(null);
        setHoverTooltipPos(null);
      }
      isPlayingRef.current = !previouslyPlaying;
      return !previouslyPlaying;
    });
  };

  const onSliderChange = (event: ChangeEvent<HTMLInputElement>) => {
    isPlayingRef.current = false;
    setIsPlaying(false);
    const value = Number(event.target.value);
    setPlayCursor(Number.isFinite(value) ? value : 0);
  };

  const toggleAllShells = (checked: boolean) => {
    if (checked) {
      setSelectedShells(new Set(allShells));
      const normalized = cloneOrbitSelection(selectedOrbitsByShell);
      for (const shell of allShells) {
        if (!normalized.has(shell)) {
          normalized.set(shell, new Set());
        }
      }
      setSelectedOrbitsByShell(normalized);
    } else {
      setSelectedShells(new Set());
    }
  };

  return (
    <div className="app-shell">
      {hoverTooltip && hoverTooltipPos && (
        <div
          className="satellite-tooltip"
          style={{
            left: hoverTooltipPos.x + 12,
            top: hoverTooltipPos.y - 12,
          }}
        >
          <div className="satellite-tooltip-title">{hoverTooltip.name}</div>
          <div>Satellite ID: {hoverTooltip.id}</div>
          <div>Shell: {hoverTooltip.shell ?? "N/A"}</div>
          <div>Orbit: {hoverTooltip.orbit ?? "N/A"}</div>
          <div>Latitude: {formatFloat(hoverTooltip.latitude, 4)}°</div>
          <div>Longitude: {formatFloat(hoverTooltip.longitude, 4)}°</div>
          <div>Altitude: {formatFloat(hoverTooltip.altitude)} km</div>
        </div>
      )}
      <div id="globeCanvas" className="globe"></div>
      <aside className="control-panel">
        <div className="title-line">
              <div className="title-main">Satellite Network Globe</div>
              <div className="title-sub">Interactive satellite network visualization</div>
              {showRouting && routeHiddenByShellFilter && (
                <div className="route-warning">The selected route is hidden by the shell/orbit filters.</div>
              )}
            </div>
        {isLoading && <p className="muted">Loading dataset...</p>}
        {error && <p className="error">{error}</p>}

        {!isLoading && (
          <>
            <section>
              <label>Dataset</label>
              <select
                value={selectedDataset}
                onChange={(e) => {
                  void switchDataset(e.target.value);
                }}
                disabled={datasetOptions.length === 0}
              >
                {datasetOptions.length === 0 ? (
                  <option value="">No datasets available</option>
                ) : (
                  datasetOptions.map((dataset) => (
                    <option key={dataset.name} value={dataset.name}>
                      {dataset.name}
                    </option>
                  ))
                )}
              </select>
            </section>

            <section>
              <label>Playback</label>
              <div className="slider-row">
                <input
                  type="range"
                  min={0}
                  max={maxFrameIndex}
                  step={0.01}
                  value={framePos}
                  onChange={onSliderChange}
                  disabled={metadata.length === 0}
                />
                <span className="value-chip">{iterationLabel}</span>
              </div>
              <div className="small-meta">time (UTC): <time dateTime={selectedTimeIso}>{selectedTime}</time></div>
              <div className="small-meta">Step: {stepSeconds === null ? "Unavailable" : `${stepSeconds.toLocaleString(undefined, { maximumFractionDigits: 6 })} s`}</div>
              {needsFastPlaybackFrames && !error && (
                <div className="small-meta" role="status">Loading frames for fast playback ({loadedFrames.length}/{metadata.length})…</div>
              )}
              <div className="button-row">
                <button type="button" onClick={onPlayToggle} disabled={!canPlay}>
                  {isPlaying ? "Pause" : "Play"}
                </button>
                <button
                  type="button"
                  onClick={() => {
                    isPlayingRef.current = false;
                    setIsPlaying(false);
                    setPlayCursor(0);
                  }}
                >
                  Reset
                </button>
              </div>
              <div className="button-row">
                <label htmlFor="speedSelect">Speed</label>
                <select
                  id="speedSelect"
                  value={playbackSpeed}
                  onChange={(e) => setPlaybackSpeed(Number(e.target.value))}
                  disabled={!canPlay}
                >
                  {PLAYBACK_SPEEDS.map((speed) => <option key={speed} value={speed}>{speed}×</option>)}
                </select>
              </div>
            </section>

            <section>
              <label>Display Layers</label>
              <div className="check-list">
                <label><input type="checkbox" checked={showSatellites} onChange={(e) => setShowSatellites(e.target.checked)} /> Satellites</label>
                <label><input type="checkbox" checked={showISL} onChange={(e) => setShowISL(e.target.checked)} /> Inter-satellite links</label>
                <label><input type="checkbox" checked={showRouting} onChange={(e) => setShowRouting(e.target.checked)} /> Routing path</label>
                <label><input type="checkbox" checked={showGs} onChange={(e) => setShowGs(e.target.checked)} /> Ground stations</label>
                <label><input type="checkbox" checked={showCrossShellOnly} onChange={(e) => setShowCrossShellOnly(e.target.checked)} /> Cross-shell links only</label>
              </div>
            </section>

            <section>
              <label>Filters</label>
              <div className="subrow">
                <div className="small-meta">Shell/Orbit</div>
                <label>
                  <input
                    type="checkbox"
                    checked={selectedShells.size === allShells.length && allShells.length > 0}
                    onChange={(e) => toggleAllShells(e.target.checked)}
                  />
                  all
                </label>
              </div>
              <div className="check-list compact shell-orbit-grid">
                {allShells.map((shell) => (
                  <div key={`shell-${shell}`} className="shell-filter-group">
                    <div className="shell-filter-header">
                      <label>
                        <input type="checkbox" checked={selectedShells.has(shell)} onChange={() => toggleShell(shell)} />
                        shell {shell}
                      </label>
                      <button
                        type="button"
                        className="shell-expand-btn"
                        onClick={() => toggleShellExpand(shell)}
                        aria-label={`Show/hide orbit options for shell ${shell}`}
                        aria-expanded={expandedShells.has(shell)}
                        aria-controls={`orbit-list-shell-${shell}`}
                      >
                        {expandedShells.has(shell) ? "▾" : "▸"}
                      </button>
                    </div>
                    <div
                      id={`orbit-list-shell-${shell}`}
                      className={"orbit-list" + (expandedShells.has(shell) ? " orbit-list-open" : " orbit-list-collapsed")}
                    >
                      {Array.from(getOrbitUniverse(shell))
                        .sort((a, b) => a - b)
                        .map((orbit) => {
                          const selectedOrbits = selectedOrbitsByShell.get(shell);
                          const isChecked = !(selectedOrbits && selectedOrbits.size > 0) || selectedOrbits.has(orbit);
                          const isDisabled = !selectedShells.has(shell);
                          return (
                            <label key={`orbit-${shell}-${orbit}`}>
                              <input
                                type="checkbox"
                                checked={isChecked}
                                disabled={isDisabled}
                                onChange={() => toggleOrbit(shell, orbit)}
                              />
                              orbit {orbit}
                            </label>
                          );
                        })}
                    </div>
                  </div>
                ))}
              </div>
            </section>

            <section>
              <label>Routing selection</label>
              <select
                value={selectedRouteIndex}
                onChange={(e) => setSelectedRouteIndex(Number(e.target.value))}
                disabled={allRoutes.length === 0}
              >
                {allRoutes.length === 0 ? (
                  <option value={0}>No routes in this frame</option>
                ) : (
                  allRoutes.map((_, idx) => <option key={`route-${idx}`} value={idx}>{`#${idx + 1}`}</option>)
                )}
              </select>
              <div className="small-meta">{routeLabel}</div>
              {currentRoute?.routing_method && (
                <div className="small-meta">Method: {currentRoute.routing_method === "VISUAL_DEMO"
                  ? "VISUAL_DEMO · visualization demo"
                  : currentRoute.routing_method}</div>
              )}
            </section>
          </>
        )}
      </aside>
    </div>
  );
}

export default App;
