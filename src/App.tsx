import { useEffect, useMemo, useState, type FormEvent } from "react";
import "./App.css";

type PrinterState = "setup" | "ready" | "starting" | "printing" | "attention" | "offline";
type ClearanceState =
  | "confirmed"
  | "occupied"
  | "awaiting_confirmation"
  | "auto_verified"
  | "unknown";
type JobState =
  | "draft"
  | "queued"
  | "assigned"
  | "starting"
  | "running"
  | "paused"
  | "completed"
  | "failed"
  | "cancelled";
type View =
  | "overview"
  | "fleet"
  | "queue"
  | "materials"
  | "files"
  | "integrations"
  | "printer";

type Printer = {
  id: string;
  name: string;
  manufacturer: string;
  model: string;
  adapter_type: string;
  host: string;
  camera_url: string;
  location: string;
  state: PrinterState;
  percent_complete: number | null;
  active_job: string | null;
  active_job_id: string | null;
  last_seen_at: string | null;
  material: string;
  supported_materials: string[];
  auto_start_enabled: boolean;
  material_color: string;
  bed_temp: number | null;
  nozzle_temp: number | null;
  run_hours: number;
  maintenance_due_hours: number;
  favorite: boolean;
  note: string;
  connection_state: "unconfigured" | "connected" | "error" | "manual";
  connection_message: string | null;
  last_refreshed_at: string | null;
  capabilities: string[];
  nozzle_diameter: number;
  tags: string[];
  filament_system: "single" | "ams" | "multi" | "external";
  loaded_filaments: string[];
  clearance_state: ClearanceState;
};
type Job = {
  id: string;
  name: string;
  filename: string;
  target_printer_id: string | null;
  material: string;
  estimated_minutes: number;
  file_id: string | null;
  state: JobState;
  created_at: string;
  queue_position: number | null;
  delivery_state: "not_ready" | "ready" | "uploaded" | "error";
  remote_file_path: string | null;
  delivered_at: string | null;
  repetitions: number | null;
  loop_mode: "sticky" | "cycle";
  auto_restart: boolean;
  auto_clear: boolean;
  dispatch_paused: boolean;
  completed_repetitions: number;
  parent_job_id: string | null;
  run_number: number | null;
  required_printer_manufacturer: string;
  required_printer_model: string;
  required_nozzle_diameter: number | null;
  required_filaments: string[];
  required_tags: string[];
  tag_match_mode: "all" | "any" | "exactly";
  allowed_printer_ids: string[];
  auto_switch_filament: boolean;
  override_existing_file: boolean;
};
type Material = {
  id: string;
  name: string;
  material_type: string;
  brand: string;
  color: string;
  remaining_percent: number;
  spool_grams: number;
  assigned_printer_id: string | null;
  created_at: string;
  updated_at: string;
};
type FileItem = {
  id: string;
  name: string;
  filename: string;
  file_type: string;
  material: string;
  estimated_minutes: number | null;
  notes: string;
  created_at: string;
  stored_filename: string | null;
  size_bytes: number | null;
  sha256: string | null;
  auto_eject_verified: boolean;
  estimated_filament_grams: number | null;
  printer_manufacturer: string;
  printer_model: string;
  nozzle_diameter: number | null;
  filament_profiles: string[];
};
type Activity = {
  id: string;
  occurred_at: string;
  level: "info" | "success" | "warning";
  message: string;
};
type RouteCandidate = {
  printer_id: string;
  printer_name: string;
  score: number;
  reason: string;
};
type HubData = {
  printers: Printer[];
  jobs: Job[];
  materials: Material[];
  files: FileItem[];
  activity: Activity[];
};

const now = new Date().toISOString();
const preview: HubData = {
  printers: [
    {
      id: "preview-simulator",
      name: "Forge Simulator",
      manufacturer: "Mirdain",
      model: "Simulator",
      adapter_type: "simulator",
      host: "simulator://local",
      camera_url: "",
      location: "Shop floor",
      state: "printing",
      percent_complete: 62,
      active_job: "Pallet clips - batch 04",
      active_job_id: "preview-run-5",
      last_seen_at: now,
      material: "PETG - Black",
      supported_materials: ["PLA", "PETG", "ABS", "ASA", "TPU"],
      auto_start_enabled: true,
      material_color: "#20252a",
      bed_temp: 80,
      nozzle_temp: 255,
      run_hours: 148,
      maintenance_due_hours: 250,
      favorite: true,
      note: "Clean build plate every week.",
      connection_state: "connected",
      connection_message:
        "Simulated printer — no physical hardware receives these preview commands.",
      last_refreshed_at: now,
      capabilities: ["telemetry", "file_delivery", "job_control"],
      nozzle_diameter: 0.4,
      tags: ["enclosed", "production", "north"],
      filament_system: "ams",
      loaded_filaments: ["PETG - Black"],
      clearance_state: "occupied",
    },
    {
      id: "preview-prusa",
      name: "Forge West",
      manufacturer: "Prusa",
      model: "MK4",
      adapter_type: "prusalink",
      host: "192.168.1.91",
      camera_url: "",
      location: "Shop floor",
      state: "attention",
      percent_complete: null,
      active_job: null,
      active_job_id: null,
      last_seen_at: now,
      material: "PETG - Orange",
      supported_materials: ["PLA", "PETG", "TPU"],
      auto_start_enabled: false,
      material_color: "#e8913a",
      bed_temp: null,
      nozzle_temp: null,
      run_hours: 91,
      maintenance_due_hours: 250,
      favorite: false,
      note: "",
      connection_state: "unconfigured",
      connection_message: null,
      last_refreshed_at: null,
      capabilities: [],
      nozzle_diameter: 0.4,
      tags: ["open-frame", "flexibles", "west"],
      filament_system: "single",
      loaded_filaments: ["PETG - Orange"],
      clearance_state: "unknown",
    },
  ],
  jobs: [
    {
      id: "preview-job-1",
      name: "Pallet clips - batch 04",
      filename: "pallet-clips-b04.3mf",
      target_printer_id: "preview-simulator",
      material: "PETG - Black",
      estimated_minutes: 184,
      file_id: "preview-file-1",
      state: "queued",
      created_at: now,
      queue_position: 1,
      delivery_state: "uploaded",
      remote_file_path: "pallet-clips-b04.3mf",
      delivered_at: now,
      repetitions: null,
      loop_mode: "cycle",
      auto_restart: true,
      auto_clear: true,
      dispatch_paused: false,
      completed_repetitions: 4,
      parent_job_id: null,
      run_number: null,
      required_printer_manufacturer: "Mirdain",
      required_printer_model: "Simulator",
      required_nozzle_diameter: 0.4,
      required_filaments: ["PETG - Black"],
      required_tags: ["enclosed"],
      tag_match_mode: "all",
      allowed_printer_ids: ["preview-simulator"],
      auto_switch_filament: false,
      override_existing_file: false,
    },
    {
      id: "preview-run-5",
      name: "Pallet clips - batch 04",
      filename: "pallet-clips-b04.3mf",
      target_printer_id: "preview-simulator",
      material: "PETG - Black",
      estimated_minutes: 184,
      file_id: "preview-file-1",
      state: "running",
      created_at: now,
      queue_position: null,
      delivery_state: "uploaded",
      remote_file_path: "pallet-clips-b04.3mf",
      delivered_at: now,
      repetitions: 1,
      loop_mode: "sticky",
      auto_restart: false,
      auto_clear: true,
      dispatch_paused: false,
      completed_repetitions: 0,
      parent_job_id: "preview-job-1",
      run_number: 5,
      required_printer_manufacturer: "Mirdain",
      required_printer_model: "Simulator",
      required_nozzle_diameter: 0.4,
      required_filaments: ["PETG - Black"],
      required_tags: ["enclosed"],
      tag_match_mode: "all",
      allowed_printer_ids: ["preview-simulator"],
      auto_switch_filament: false,
      override_existing_file: false,
    },
    {
      id: "preview-job-2",
      name: "Fixture arm - left",
      filename: "fixture-arm-left.gcode",
      target_printer_id: "preview-prusa",
      material: "PETG - Orange",
      estimated_minutes: 92,
      file_id: null,
      state: "queued",
      created_at: now,
      queue_position: 2,
      delivery_state: "not_ready",
      remote_file_path: null,
      delivered_at: null,
      repetitions: 1,
      loop_mode: "sticky",
      auto_restart: false,
      auto_clear: false,
      dispatch_paused: false,
      completed_repetitions: 0,
      parent_job_id: null,
      run_number: null,
      required_printer_manufacturer: "Prusa",
      required_printer_model: "MK4",
      required_nozzle_diameter: 0.4,
      required_filaments: ["PETG - Orange"],
      required_tags: ["flexibles"],
      tag_match_mode: "all",
      allowed_printer_ids: ["preview-prusa"],
      auto_switch_filament: false,
      override_existing_file: false,
    },
    {
      id: "preview-job-3",
      name: "Cable guide set",
      filename: "cable-guides-v3.3mf",
      target_printer_id: null,
      material: "PLA - White",
      estimated_minutes: 48,
      file_id: null,
      state: "queued",
      created_at: now,
      queue_position: 3,
      delivery_state: "not_ready",
      remote_file_path: null,
      delivered_at: null,
      repetitions: 1,
      loop_mode: "sticky",
      auto_restart: false,
      auto_clear: false,
      dispatch_paused: true,
      completed_repetitions: 0,
      parent_job_id: null,
      run_number: null,
      required_printer_manufacturer: "",
      required_printer_model: "",
      required_nozzle_diameter: 0.4,
      required_filaments: ["PLA - White"],
      required_tags: ["production"],
      tag_match_mode: "any",
      allowed_printer_ids: [],
      auto_switch_filament: false,
      override_existing_file: false,
    },
  ],
  materials: [
    {
      id: "preview-material-1",
      name: "Black PETG",
      material_type: "PETG",
      brand: "Polymaker",
      color: "#20252a",
      remaining_percent: 43,
      spool_grams: 1000,
      assigned_printer_id: "preview-simulator",
      created_at: now,
      updated_at: now,
    },
    {
      id: "preview-material-2",
      name: "White PLA",
      material_type: "PLA",
      brand: "Overture",
      color: "#e5e4dd",
      remaining_percent: 18,
      spool_grams: 1000,
      assigned_printer_id: null,
      created_at: now,
      updated_at: now,
    },
  ],
  files: [
    {
      id: "preview-file-1",
      name: "Pallet clips - batch 04",
      filename: "pallet-clips-b04.3mf",
      file_type: "3mf",
      material: "PETG - Black",
      estimated_minutes: 184,
      notes: "Validated with the no-hardware Mirdain simulator profile.",
      created_at: now,
      stored_filename: "preview.3mf",
      size_bytes: 1640000,
      sha256: "preview",
      auto_eject_verified: true,
      estimated_filament_grams: 178,
      printer_manufacturer: "Mirdain",
      printer_model: "Simulator",
      nozzle_diameter: 0.4,
      filament_profiles: ["PETG - Black"],
    },
  ],
  activity: [
    {
      id: "preview-1",
      occurred_at: now,
      level: "success",
      message: "Forge Simulator confirmed its active preview job.",
    },
    {
      id: "preview-2",
      occurred_at: now,
      level: "info",
      message: "Fixture arm - left entered the queue.",
    },
    {
      id: "preview-3",
      occurred_at: now,
      level: "warning",
      message: "White PLA is below the restock threshold.",
    },
  ],
};

const labels: Record<PrinterState | JobState, string> = {
  setup: "Setup needed",
  ready: "Ready",
  printing: "Printing",
  attention: "Needs review",
  offline: "Offline",
  draft: "Draft",
  queued: "Queued",
  assigned: "Assigned",
  starting: "Starting",
  running: "Running",
  paused: "Paused",
  completed: "Complete",
  failed: "Failed",
  cancelled: "Cancelled",
};

function relativeTime(timestamp: string | null) {
  if (!timestamp) return "not seen yet";
  const minutes = Math.max(
    0,
    Math.round((Date.now() - new Date(timestamp).getTime()) / 60000),
  );
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes}m ago`;
  if (minutes < 1440) return `${Math.round(minutes / 60)}h ago`;
  return `${Math.round(minutes / 1440)}d ago`;
}
function duration(minutes: number | null) {
  if (!minutes) return "-";
  const hours = Math.floor(minutes / 60);
  return hours ? `${hours}h ${minutes % 60}m` : `${minutes}m`;
}
function fileSize(bytes: number | null) {
  if (bytes === null) return "Catalog record";
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}
function runPlan(job: Job) {
  const total = job.repetitions === null ? "∞" : String(job.repetitions);
  const mode = job.loop_mode === "sticky" ? "priority loop" : "rotation loop";
  return `${job.completed_repetitions}/${total} runs · ${mode}`;
}
function commaList(value: string, uppercase = false) {
  return value
    .split(",")
    .map((item) => (uppercase ? item.trim().toUpperCase() : item.trim()))
    .filter(Boolean)
    .slice(0, 24);
}
function materialList(value: string) {
  return commaList(value, true);
}
function filamentSystemLabel(value: Printer["filament_system"] | undefined) {
  return (
    {
      single: "Single spool",
      ams: "AMS",
      multi: "Multi-material",
      external: "External feeder",
    }[value ?? "single"] ?? "Single spool"
  );
}
function supportsUnattendedStart(printer: Printer) {
  return ["moonraker", "prusalink", "simulator"].includes(
    printer.adapter_type,
  );
}
function clearanceLabel(value: ClearanceState | undefined) {
  return (
    {
      confirmed: "Bed clear",
      occupied: "Bed occupied",
      awaiting_confirmation: "Awaiting bed clearance",
      auto_verified: "Auto-clear verified",
      unknown: "Bed clearance unknown",
    }[value ?? "unknown"] ?? "Bed clearance unknown"
  );
}
function parentRunStats(parent: Job, jobs: Job[]) {
  const children = jobs.filter((job) => job.parent_job_id === parent.id);
  const active = children.filter((job) =>
    ["assigned", "starting", "running", "paused"].includes(job.state),
  ).length;
  return {
    active,
    completed: parent.completed_repetitions,
    total: parent.repetitions === null ? "∞" : String(parent.repetitions),
  };
}
function jobRules(job: Job, sourceFile: FileItem | null = null) {
  const rules: string[] = [];
  const requiredManufacturer =
    job.required_printer_manufacturer.trim() ||
    sourceFile?.printer_manufacturer.trim() ||
    "";
  const requiredModel =
    job.required_printer_model.trim() ||
    sourceFile?.printer_model.trim() ||
    "";
  const requiredNozzle =
    job.required_nozzle_diameter ?? sourceFile?.nozzle_diameter ?? null;
  const requiredFilaments = job.required_filaments?.length
    ? job.required_filaments
    : (sourceFile?.filament_profiles ?? []);
  const materialName =
    job.material.trim() || sourceFile?.material.trim() || "";
  if (requiredManufacturer) rules.push(`Make ${requiredManufacturer}`);
  if (requiredModel) rules.push(`Model ${requiredModel}`);
  if (requiredNozzle !== null) rules.push(`${requiredNozzle} mm nozzle`);
  if (requiredFilaments.length) rules.push(requiredFilaments.join(" + "));
  else if (materialName) rules.push(`Material ${materialType(materialName)}`);
  if (job.required_tags?.length) {
    const mode =
      job.tag_match_mode === "any"
        ? "Any"
        : job.tag_match_mode === "exactly"
          ? "Exactly"
          : "All";
    rules.push(`${mode} tags: ${job.required_tags.join(", ")}`);
  }
  if (job.allowed_printer_ids?.length)
    rules.push(
      `${job.allowed_printer_ids.length} allowed printer${job.allowed_printer_ids.length === 1 ? "" : "s"}`,
    );
  return rules;
}
function normalizedProfile(value: string) {
  return value.trim().toLowerCase().replace(/\s+/g, " ");
}
function materialType(value: string) {
  let normalized = value.trim().toUpperCase();
  for (const separator of ["·", "-", "/", ","]) {
    normalized = normalized.split(separator, 1)[0].trim();
  }
  return normalized;
}
function filamentProfilesMatch(required: string[], loaded: string[]) {
  const normalizedRequired = required
    .map(normalizedProfile)
    .filter(Boolean);
  const normalizedLoaded = loaded.map(normalizedProfile).filter(Boolean);
  if (normalizedRequired.length !== normalizedLoaded.length) return false;
  return normalizedRequired.every(
    (profile, index) => profile === normalizedLoaded[index],
  );
}
function tagsMatch(
  printerTags: string[],
  requiredTags: string[],
  mode: Job["tag_match_mode"],
) {
  const available = new Set(
    printerTags.map(normalizedProfile).filter(Boolean),
  );
  const required = new Set(
    requiredTags.map(normalizedProfile).filter(Boolean),
  );
  if (!required.size) return true;
  if (mode === "any") {
    return [...required].some((tag) => available.has(tag));
  }
  if (mode === "exactly") {
    return (
      required.size === available.size &&
      [...required].every((tag) => available.has(tag))
    );
  }
  return [...required].every((tag) => available.has(tag));
}
function isJobCompatible(
  job: Job,
  printer: Printer,
  sourceFile: FileItem | null,
) {
  if (
    printer.state !== "ready" ||
    printer.connection_state !== "connected" ||
    !["confirmed", "auto_verified"].includes(printer.clearance_state) ||
    !printer.capabilities.includes("file_transfer")
  )
    return false;
  if (
    job.allowed_printer_ids?.length &&
    !job.allowed_printer_ids.includes(printer.id)
  )
    return false;
  const requiredManufacturer =
    job.required_printer_manufacturer.trim() ||
    sourceFile?.printer_manufacturer.trim() ||
    "";
  const requiredModel =
    job.required_printer_model.trim() ||
    sourceFile?.printer_model.trim() ||
    "";
  const requiredNozzle =
    job.required_nozzle_diameter ?? sourceFile?.nozzle_diameter ?? null;
  const requiredFilaments =
    job.required_filaments?.length
      ? job.required_filaments
      : (sourceFile?.filament_profiles ?? []);
  if (
    requiredManufacturer &&
    requiredManufacturer.toLowerCase() !==
      printer.manufacturer.trim().toLowerCase()
  )
    return false;
  if (
    requiredModel &&
    requiredModel.toLowerCase() !== printer.model.trim().toLowerCase()
  )
    return false;
  if (
    requiredNozzle !== null &&
    Math.abs((printer.nozzle_diameter ?? 0) - requiredNozzle) > 0.0001
  )
    return false;
  const loaded = (printer.loaded_filaments ?? []).length
    ? printer.loaded_filaments
    : printer.material.trim()
      ? [printer.material]
      : [];
  if (
    requiredFilaments.length &&
    !filamentProfilesMatch(requiredFilaments, loaded)
  )
    return false;
  if (!tagsMatch(printer.tags ?? [], job.required_tags ?? [], job.tag_match_mode))
    return false;
  if (!requiredFilaments.length) {
    const materialName =
      job.material.trim() || sourceFile?.material.trim() || "";
    const requiredMaterial = materialType(materialName);
    const loadedMaterial = materialType(printer.material);
    const supportedMaterials = new Set(
      (printer.supported_materials ?? [])
        .map(materialType)
        .filter(Boolean),
    );
    if (
      requiredMaterial &&
      requiredMaterial !== loadedMaterial &&
      !supportedMaterials.has(requiredMaterial)
    )
      return false;
  }
  return true;
}
function isFileCompatible(file: FileItem, printer: Printer) {
  if (
    file.printer_manufacturer &&
    file.printer_manufacturer.toLowerCase() !==
      printer.manufacturer.toLowerCase()
  )
    return false;
  if (
    file.printer_model &&
    file.printer_model.toLowerCase() !== printer.model.toLowerCase()
  )
    return false;
  if (
    file.nozzle_diameter &&
    Math.abs((printer.nozzle_diameter ?? 0) - file.nozzle_diameter) > 0.001
  )
    return false;
  return (
    !(file.filament_profiles ?? []).length ||
    filamentProfilesMatch(
      file.filament_profiles,
      (printer.loaded_filaments ?? []).length
        ? printer.loaded_filaments
        : printer.material.trim()
          ? [printer.material]
          : [],
    )
  );
}
function statusClass(state: PrinterState | JobState) {
  return `status-pill ${state}`;
}

async function api(path: string, options?: RequestInit) {
  const response = await fetch(path, {
    ...options,
    headers: { "Content-Type": "application/json", ...options?.headers },
  });
  if (!response.ok) {
    const body = (await response.json().catch(() => null)) as {
      detail?: string;
    } | null;
    throw new Error(
      body?.detail || "The local service could not complete that action.",
    );
  }
  return response.status === 204 ? null : response.json();
}

async function uploadFile(form: FormData) {
  const response = await fetch("/api/files/upload", {
    method: "POST",
    body: form,
  });
  if (!response.ok) {
    const body = (await response.json().catch(() => null)) as {
      detail?: string;
    } | null;
    throw new Error(
      body?.detail || "The file could not be uploaded to the local library.",
    );
  }
  return response.json();
}

function App() {
  const [data, setData] = useState<HubData>(preview);
  const [serviceMode, setServiceMode] = useState<"preview" | "local" | "offline">(
    "preview",
  );
  const [view, setView] = useState<View>("overview");
  const [selectedPrinterId, setSelectedPrinterId] = useState<string | null>(
    null,
  );
  const [modal, setModal] = useState<
    "printer" | "job" | "material" | "file" | null
  >(null);
  const [connectionPrinterId, setConnectionPrinterId] = useState<string | null>(
    null,
  );
  const [notice, setNotice] = useState<string | null>(null);
  const [jobSeed, setJobSeed] = useState<Partial<Job> | null>(null);
  const [editingJobId, setEditingJobId] = useState<string | null>(null);
  const [routeReview, setRouteReview] = useState<{
    job: Job;
    candidates: RouteCandidate[];
  } | null>(null);

  const load = async () => {
    try {
      const payload = (await api("/api/dashboard")) as Partial<HubData>;
      setData({
        printers: Array.isArray(payload.printers) ? payload.printers : [],
        jobs: Array.isArray(payload.jobs) ? payload.jobs : [],
        materials: Array.isArray(payload.materials) ? payload.materials : [],
        files: Array.isArray(payload.files) ? payload.files : [],
        activity: Array.isArray(payload.activity) ? payload.activity : [],
      });
      setServiceMode("local");
    } catch {
      setServiceMode((current) => (current === "local" ? "offline" : current));
    }
  };
  useEffect(() => {
    void load();
    const refresh = window.setInterval(() => void load(), 15_000);
    return () => window.clearInterval(refresh);
  }, []);

  const summary = useMemo(
    () => ({
      printing: data.printers.filter((item) => item.state === "printing")
        .length,
      ready: data.printers.filter((item) => item.state === "ready").length,
      attention: data.printers.filter((item) =>
        ["attention", "setup", "offline"].includes(item.state),
      ).length,
      queued: data.jobs.filter(
        (item) => item.state === "queued" && item.parent_job_id === null,
      ).length,
    }),
    [data],
  );
  const queued = useMemo(
    () =>
      data.jobs
        .filter(
          (item) => item.state === "queued" && item.parent_job_id === null,
        )
        .sort(
          (a, b) => (a.queue_position ?? 99999) - (b.queue_position ?? 99999),
        ),
    [data.jobs],
  );
  const selected =
    data.printers.find((item) => item.id === selectedPrinterId) ?? null;
  const lowMaterials = data.materials.filter(
    (item) => item.remaining_percent < 20,
  );
  const openNewJob = (seed: Partial<Job> | null = null) => {
    setEditingJobId(null);
    setJobSeed(seed);
    setModal("job");
  };
  const openJobEditor = (job: Job) => {
    setEditingJobId(job.id);
    setJobSeed(job);
    setModal("job");
  };

  const changeView = (next: Exclude<View, "printer">) => {
    setView(next);
    setSelectedPrinterId(null);
  };
  const selectPrinter = (printerId: string) => {
    setSelectedPrinterId(printerId);
    setView("printer");
    window.scrollTo({ top: 0, behavior: "smooth" });
  };
  const mutate = async (work: () => Promise<unknown>, success: string) => {
    try {
      await work();
      setNotice(success);
      setModal(null);
      setJobSeed(null);
      setEditingJobId(null);
      await load();
    } catch (error) {
      setNotice(
        error instanceof Error
          ? error.message
          : "Action could not be completed.",
      );
    }
  };
  const remove = async (path: string, label: string) => {
    if (
      !window.confirm(
        `Remove ${label}? This only affects this local workspace.`,
      )
    )
      return;
    await mutate(
      () => api(path, { method: "DELETE" }),
      `${label} was removed.`,
    );
  };
  const moveQueue = async (job: Job, direction: -1 | 1) => {
    const index = queued.findIndex((item) => item.id === job.id);
    const target = queued[index + direction];
    if (!target) return;
    await mutate(async () => {
      await api(`/api/jobs/${target.id}`, {
        method: "PATCH",
        body: JSON.stringify({ queue_position: job.queue_position }),
      });
      return api(`/api/jobs/${job.id}`, {
        method: "PATCH",
        body: JSON.stringify({ queue_position: target.queue_position }),
      });
    }, "Queue order was updated.");
  };
  const openJobForFile = (file: FileItem) => {
    openNewJob({
      name: file.name,
      filename: file.filename,
      file_id: file.id,
      material: file.material,
      estimated_minutes: file.estimated_minutes ?? 60,
      required_printer_manufacturer: file.printer_manufacturer,
      required_printer_model: file.printer_model,
      required_nozzle_diameter: file.nozzle_diameter,
      required_filaments: file.filament_profiles,
    });
  };
  const reviewRoute = async (job: Job) => {
    try {
      const candidates = (await api(
        `/api/jobs/${job.id}/route-candidates`,
      )) as RouteCandidate[];
      if (!candidates.length) {
        setNotice("No ready connected printer meets this job's current material requirements.");
        return;
      }
      setRouteReview({ job, candidates });
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "Routes could not be checked.");
    }
  };

  return (
    <div className="app-shell">
      <header className="app-header">
        <button
          className="wordmark"
          onClick={() => changeView("overview")}
          aria-label="Open Mirdain Print Hub"
        >
          <span className="wordmark-mark" aria-hidden="true">
            MP
          </span>
          <span>
            MIRDAIN <strong>PRINT HUB</strong>
          </span>
        </button>
        <nav className="main-nav" aria-label="Primary navigation">
          <button
            className={view === "overview" ? "active" : ""}
            onClick={() => changeView("overview")}
          >
            Overview
          </button>
          <button
            className={view === "fleet" || view === "printer" ? "active" : ""}
            onClick={() => changeView("fleet")}
          >
            Fleet
          </button>
          <button
            className={view === "queue" ? "active" : ""}
            onClick={() => changeView("queue")}
          >
            Queue <span>{summary.queued}</span>
          </button>
          <button
            className={view === "files" ? "active" : ""}
            onClick={() => changeView("files")}
          >
            Files <span>{data.files.length}</span>
          </button>
          <button
            className={view === "materials" ? "active" : ""}
            onClick={() => changeView("materials")}
          >
            Materials <span>{data.materials.length}</span>
          </button>
          <button
            className={view === "integrations" ? "active" : ""}
            onClick={() => changeView("integrations")}
          >
            Integrations
          </button>
        </nav>
        <div className="header-actions">
          <span className={`service-chip ${serviceMode}`}>
            <i />
            {serviceMode === "local"
              ? "Local workspace"
              : serviceMode === "offline"
                ? "Last local sync"
                : "Preview mode"}
          </span>
          <button className="button quiet" onClick={() => setModal("printer")}>
            Add printer
          </button>
          <button className="button primary" onClick={() => openNewJob()}>
            Queue job
          </button>
        </div>
      </header>

      {serviceMode === "preview" && (
        <div className="workspace-banner">
          <strong>Preview data is shown.</strong>
          <span>
            Start the local service to save your fleet, jobs, materials, and
            files on this computer.
          </span>
        </div>
      )}
      {serviceMode === "offline" && (
        <div className="workspace-banner offline-banner">
          <strong>Local service is temporarily unavailable.</strong>
          <span>Showing the last data received from this computer. Mirdain will reconnect automatically.</span>
        </div>
      )}
      {notice && (
        <button
          className="notice floating-notice"
          onClick={() => setNotice(null)}
        >
          {notice}
          <span>Dismiss</span>
        </button>
      )}

      {view === "overview" && (
        <Overview
          data={data}
          summary={summary}
          lowMaterials={lowMaterials}
          onView={changeView}
          onPrinter={selectPrinter}
          onAddPrinter={() => setModal("printer")}
          onAddJob={() => openNewJob()}
        />
      )}
      {view === "queue" && (
        <QueueView
          jobs={data.jobs}
          printers={data.printers}
          files={data.files}
          onAdd={() => openNewJob()}
          onEdit={openJobEditor}
          onResumeDispatch={(job) =>
            mutate(
              () =>
                api(`/api/jobs/${job.id}`, {
                  method: "PATCH",
                  body: JSON.stringify({ dispatch_paused: false }),
                }),
              `${job.name} dispatch was resumed.`,
            )
          }
          onDisarmAutomation={(job) =>
            mutate(
              () =>
                api(`/api/jobs/${job.id}`, {
                  method: "PATCH",
                  body: JSON.stringify({
                    auto_restart: false,
                    auto_clear: false,
                    dispatch_paused: true,
                  }),
                }),
              `${job.name} automation was disarmed and future dispatch is paused.`,
            )
          }
          onMove={moveQueue}
          onDeliver={(job) =>
            mutate(
              () => api(`/api/jobs/${job.id}/deliver`, { method: "POST" }),
              `${job.name} was uploaded to its assigned printer.`,
            )
          }
          onAutoRoute={reviewRoute}
          onComplete={(job) =>
            mutate(
              () =>
                api(`/api/jobs/${job.id}/complete`, {
                  method: "POST",
                  body: JSON.stringify({ confirmation: "COMPLETE" }),
                }),
              `${job.name}${job.run_number ? ` run ${job.run_number}` : ""} was recorded as complete. Inspect the bed and confirm READY before dispatching again.`,
            )
          }
          onControl={(job, action) =>
            mutate(
              () =>
                api(`/api/jobs/${job.id}/control`, {
                  method: "POST",
                  body: JSON.stringify({
                    action,
                    confirmation: action.toUpperCase(),
                  }),
                }),
              `${job.name} received the confirmed ${action} command.`,
            )
          }
          onRemove={(job) => remove(`/api/jobs/${job.id}`, job.name)}
        />
      )}
      {view === "fleet" && (
        <FleetView
          printers={data.printers}
          jobs={data.jobs}
          files={data.files}
          onPrinter={selectPrinter}
          onAdd={() => setModal("printer")}
        />
      )}
      {view === "materials" && (
        <MaterialsView
          materials={data.materials}
          printers={data.printers}
          onAdd={() => setModal("material")}
          onRemove={(material) =>
            remove(`/api/materials/${material.id}`, material.name)
          }
        />
      )}
      {view === "files" && (
        <FilesView
          files={data.files}
          printers={data.printers}
          onAdd={() => setModal("file")}
          onQueue={openJobForFile}
          onRemove={(file) => remove(`/api/files/${file.id}`, file.name)}
        />
      )}
      {view === "integrations" && (
        <IntegrationsView serviceMode={serviceMode} />
      )}
      {view === "printer" && selected && (
        <PrinterView
          printer={selected}
          jobs={data.jobs}
          onBack={() => changeView("fleet")}
          onUpdate={(payload, message) =>
            mutate(
              () =>
                api(`/api/printers/${selected.id}`, {
                  method: "PATCH",
                  body: JSON.stringify(payload),
                }),
              message,
            )
          }
          onArmAutomation={() =>
            mutate(
              () =>
                api(`/api/printers/${selected.id}/automation/arm`, {
                  method: "POST",
                  body: JSON.stringify({
                    confirmation: "ARM STATIC ENDPOINT",
                  }),
                }),
              `${selected.name} verified auto-start is armed.`,
            )
          }
          onRemove={() => remove(`/api/printers/${selected.id}`, selected.name)}
          onAddJob={() => {
            openNewJob({
              target_printer_id: selected.id,
              material: selected.material,
            });
          }}
          onConnect={() => setConnectionPrinterId(selected.id)}
          onRefresh={() =>
            mutate(
              () =>
                api(`/api/printers/${selected.id}/refresh`, { method: "POST" }),
              `${selected.name} telemetry was refreshed.`,
            )
          }
          onReady={() =>
            mutate(
              () =>
                api(`/api/printers/${selected.id}/ready`, {
                  method: "POST",
                  body: JSON.stringify({ confirmation: "READY" }),
                }),
              `${selected.name} bed clearance was confirmed and the printer is ready.`,
            )
          }
        />
      )}

      {modal === "printer" && (
        <PrinterModal
          onClose={() => setModal(null)}
          onSubmit={(payload) =>
            mutate(
              () =>
                api("/api/printers", {
                  method: "POST",
                  body: JSON.stringify(payload),
                }),
              `${payload.name} was added to the local fleet.`,
            )
          }
        />
      )}
      {modal === "job" && (
        <JobModal
          printers={data.printers}
          files={data.files}
          seed={jobSeed}
          editing={Boolean(editingJobId)}
          onClose={() => {
            setModal(null);
            setJobSeed(null);
            setEditingJobId(null);
          }}
          onSubmit={(payload) =>
            mutate(
              () =>
                api(editingJobId ? `/api/jobs/${editingJobId}` : "/api/jobs", {
                  method: editingJobId ? "PATCH" : "POST",
                  body: JSON.stringify(payload),
                }),
              editingJobId
                ? `${payload.name} was updated.`
                : `${payload.name} was added to the queue.`,
            )
          }
        />
      )}
      {modal === "material" && (
        <MaterialModal
          printers={data.printers}
          onClose={() => setModal(null)}
          onSubmit={(payload) =>
            mutate(
              () =>
                api("/api/materials", {
                  method: "POST",
                  body: JSON.stringify(payload),
                }),
              `${payload.name} was added to materials.`,
            )
          }
        />
      )}
      {modal === "file" && (
        <FileModal
          onClose={() => setModal(null)}
          onSubmit={(form) =>
            mutate(
              () => uploadFile(form),
              `${String(form.get("name"))} was uploaded to the local file library.`,
            )
          }
        />
      )}
      {connectionPrinterId &&
        data.printers.find((printer) => printer.id === connectionPrinterId) && (
          <ConnectionModal
            printer={
              data.printers.find(
                (printer) => printer.id === connectionPrinterId,
              )!
            }
            onClose={() => setConnectionPrinterId(null)}
            onSubmit={(payload) =>
              mutate(
                () =>
                  api(`/api/printers/${connectionPrinterId}/connection`, {
                    method: "POST",
                    body: JSON.stringify(payload),
                  }),
                "Local printer connection verified. Monitoring is now enabled.",
              ).finally(() => setConnectionPrinterId(null))
            }
          />
        )}
      {routeReview && (
        <RouteReviewModal
          job={routeReview.job}
          candidates={routeReview.candidates}
          onClose={() => setRouteReview(null)}
          onConfirm={(printerId) => {
            setRouteReview(null);
            void mutate(
              () =>
                api(`/api/jobs/${routeReview.job.id}/route`, {
                  method: "POST",
                  body: JSON.stringify({ printer_id: printerId }),
                }),
              `${routeReview.job.name} was routed to the selected compatible printer.`,
            );
          }}
        />
      )}
    </div>
  );
}

function Overview({
  data,
  summary,
  lowMaterials,
  onView,
  onPrinter,
  onAddPrinter,
  onAddJob,
}: {
  data: HubData;
  summary: Record<string, number>;
  lowMaterials: Material[];
  onView: (view: Exclude<View, "printer">) => void;
  onPrinter: (id: string) => void;
  onAddPrinter: () => void;
  onAddJob: () => void;
}) {
  return (
    <main className="home-main">
      <section className="overview-heading">
        <div>
          <p className="eyebrow">LOCAL PRINT OPERATIONS</p>
          <h1>Run your print floor from one private workspace.</h1>
          <p className="lede">
            Fleet health, a shared production queue, materials, service
            reminders, and a file library - all stored locally on your computer.
          </p>
        </div>
        <div className="overview-state">
          <span>Fleet health</span>
          <strong>
            {summary.attention
              ? `${summary.attention} need attention`
              : "All clear"}
          </strong>
          <small>{data.printers.length} printers in this workspace</small>
        </div>
      </section>
      <section className="metric-row" aria-label="Fleet summary">
        <Metric
          label="Printing now"
          value={summary.printing}
          detail="active production jobs"
          tone="amber"
        />
        <Metric
          label="Ready"
          value={summary.ready}
          detail="available for verified jobs"
        />
        <Metric
          label="Needs attention"
          value={summary.attention}
          detail="offline or needs setup"
          tone={summary.attention ? "warning" : undefined}
        />
        <Metric
          label="Queue depth"
          value={summary.queued}
          detail="jobs waiting to run"
        />
      </section>
      <section className="printer-view">
        <div className="section-heading">
          <div>
            <p className="eyebrow">FLEET STATUS</p>
            <h2>
              Printers <span>{data.printers.length}</span>
            </h2>
          </div>
          <button className="text-button" onClick={onAddPrinter}>
            Add a printer <span>-&gt;</span>
          </button>
        </div>
        <div className="fleet-grid-wrap">
          {data.printers.length ? (
            <div className="fleet-card-grid">
              {data.printers.map((printer) => (
                <button
                  className="fleet-card"
                  key={printer.id}
                  onClick={() => onPrinter(printer.id)}
                >
                  <span className="fleet-card-top">
                    <span className="printer-cell">
                      <i className={`state-dot ${printer.state}`} />
                      <span>
                        <strong>{printer.name}</strong>
                        <small>{printer.manufacturer} / {printer.model}</small>
                      </span>
                    </span>
                    <span className={statusClass(printer.state)}>
                      {labels[printer.state]}
                    </span>
                  </span>
                  {["awaiting_confirmation", "unknown"].includes(
                    printer.clearance_state ?? "unknown",
                  ) && (
                    <span className="clearance-flag">
                      {clearanceLabel(printer.clearance_state)}
                    </span>
                  )}
                  <span className="fleet-card-job">
                    <small>{printer.state === "printing" ? "ACTIVE JOB" : "NEXT ACTION"}</small>
                    <strong>{printer.active_job || (printer.state === "ready" ? "Ready for a compatible job" : "Review connection or setup")}</strong>
                    {printer.percent_complete !== null && (
                      <span className="fleet-progress">
                        <i style={{ width: `${printer.percent_complete}%` }} />
                        <em>{printer.percent_complete}%</em>
                      </span>
                    )}
                  </span>
                  <span className="fleet-card-meta">
                    <span>
                      <small>MATERIAL</small>
                      <b>{printer.material || "Not loaded"}</b>
                    </span>
                    <span>
                      <small>LOCAL SYNC</small>
                      <b>{relativeTime(printer.last_refreshed_at)}</b>
                    </span>
                  </span>
                  <span className="fleet-card-footer">
                    {printer.capabilities.length ? `${printer.capabilities.length} live capabilities` : "Configure local connection"}
                    <i>Open</i>
                  </span>
                </button>
              ))}
            </div>
          ) : (
            <EmptyState
              title="Bring your first printer online."
              copy="Add a printer to start a local fleet record."
              action="Add printer"
              onAction={onAddPrinter}
            />
          )}
        </div>
      </section>
      <section className="operations-grid">
        <section className="surface-panel queue-panel">
          <div className="section-heading compact">
            <div>
              <p className="eyebrow">PRODUCTION QUEUE</p>
              <h2>Next to run</h2>
            </div>
            <button className="text-button" onClick={() => onView("queue")}>
              Open queue <span>-&gt;</span>
            </button>
          </div>
          <QueuePreview data={data} onAdd={onAddJob} />
        </section>
        <section className="surface-panel activity-panel">
          <div className="section-heading compact">
            <div>
              <p className="eyebrow">WATCH LIST</p>
              <h2>Materials</h2>
            </div>
            <button className="text-button" onClick={() => onView("materials")}>
              Open shelf
            </button>
          </div>
          <div className="activity-list">
            {lowMaterials.length ? (
              lowMaterials.slice(0, 3).map((material) => (
                <article className="activity-item" key={material.id}>
                  <i className="warning" />
                  <div>
                    <p>
                      <strong>{material.name}</strong> is at{" "}
                      {material.remaining_percent}%.
                    </p>
                    <time>{material.spool_grams}g spool · restock soon</time>
                  </div>
                </article>
              ))
            ) : (
              <EmptyState
                title="No material alerts."
                copy="Your registered spools are above the restock threshold."
              />
            )}
          </div>
        </section>
      </section>
      <section className="surface-panel recent-panel">
        <div className="section-heading compact">
          <div>
            <p className="eyebrow">OPERATIONS LOG</p>
            <h2>What changed</h2>
          </div>
        </div>
        <div className="activity-list">
          {data.activity.slice(0, 5).map((item) => (
            <article className="activity-item" key={item.id}>
              <i className={item.level} />
              <div>
                <p>{item.message}</p>
                <time>{relativeTime(item.occurred_at)}</time>
              </div>
            </article>
          ))}
        </div>
      </section>
    </main>
  );
}

function FleetView({
  printers,
  jobs,
  files,
  onPrinter,
  onAdd,
}: {
  printers: Printer[];
  jobs: Job[];
  files: FileItem[];
  onPrinter: (id: string) => void;
  onAdd: () => void;
}) {
  const [search, setSearch] = useState("");
  const [stateFilter, setStateFilter] = useState<"all" | PrinterState>("all");
  const queuedJobs = jobs.filter(
    (job) => job.state === "queued" && job.parent_job_id === null,
  );
  const filtered = printers.filter((printer) => {
    const haystack = [
      printer.name,
      printer.manufacturer,
      printer.model,
      printer.location,
      ...(printer.tags ?? []),
      ...(printer.loaded_filaments ?? []),
    ]
      .join(" ")
      .toLowerCase();
    return (
      (stateFilter === "all" || printer.state === stateFilter) &&
      haystack.includes(search.trim().toLowerCase())
    );
  });
  return (
    <main className="home-main view-main">
      <section className="view-heading fleet-heading">
        <div>
          <p className="eyebrow">FLEET DIRECTORY</p>
          <h1>Every machine, capability, and loaded filament in one view.</h1>
          <p className="lede">
            Compatibility metadata lets the queue route work by model, nozzle,
            filament, tags, and explicit printer restrictions.
          </p>
        </div>
        <button className="button primary" onClick={onAdd}>
          Add printer
        </button>
      </section>
      <section className="fleet-toolbar" aria-label="Fleet filters">
        <label>
          Search fleet
          <input
            type="search"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder="Name, model, tag, or filament"
          />
        </label>
        <label>
          Printer status
          <select
            value={stateFilter}
            onChange={(event) =>
              setStateFilter(event.target.value as "all" | PrinterState)
            }
          >
            <option value="all">All statuses</option>
            <option value="ready">Ready</option>
            <option value="printing">Printing</option>
            <option value="attention">Needs review</option>
            <option value="setup">Setup needed</option>
            <option value="offline">Offline</option>
          </select>
        </label>
        <p>
          <strong>{filtered.length}</strong>
          <span>of {printers.length} printers shown</span>
        </p>
      </section>
      {filtered.length ? (
        <section className="fleet-card-grid fleet-directory">
          {filtered.map((printer) => {
            const matches = queuedJobs.filter((job) => {
              const sourceFile =
                files.find((file) => file.id === job.file_id) ?? null;
              return isJobCompatible(job, printer, sourceFile);
            }).length;
            return (
              <button
                className="fleet-card fleet-card-detailed"
                key={printer.id}
                onClick={() => onPrinter(printer.id)}
                aria-label={`Open ${printer.name}`}
              >
                <span className="fleet-card-top">
                  <span className="printer-cell">
                    <i className={`state-dot ${printer.state}`} />
                    <span>
                      <strong>{printer.name}</strong>
                      <small>
                        {printer.manufacturer} / {printer.model}
                      </small>
                    </span>
                  </span>
                  <span className={statusClass(printer.state)}>
                    {labels[printer.state]}
                  </span>
                </span>
                {["awaiting_confirmation", "unknown"].includes(
                  printer.clearance_state ?? "unknown",
                ) && (
                  <span className="clearance-flag">
                    {clearanceLabel(printer.clearance_state)}
                  </span>
                )}
                <span className="fleet-card-job">
                  <small>
                    {printer.state === "printing"
                      ? "ACTIVE JOB"
                      : "READY ROUTES"}
                  </small>
                  <strong>
                    {printer.active_job ||
                      `${matches} routable job${matches === 1 ? "" : "s"}`}
                  </strong>
                  {printer.percent_complete !== null && (
                    <span className="fleet-progress">
                      <i style={{ width: `${printer.percent_complete}%` }} />
                      <em>{printer.percent_complete}%</em>
                    </span>
                  )}
                </span>
                <span className="compatibility-facts">
                  <span>
                    <small>NOZZLE</small>
                    <b>{printer.nozzle_diameter ?? 0.4} mm</b>
                  </span>
                  <span>
                    <small>FILAMENT</small>
                    <b>{filamentSystemLabel(printer.filament_system)}</b>
                  </span>
                  <span>
                    <small>LOADED</small>
                    <b>
                      {(printer.loaded_filaments ?? []).join(", ") ||
                        printer.material ||
                        "None"}
                    </b>
                  </span>
                </span>
                <span className="tag-list" aria-label="Printer tags">
                  {(printer.tags ?? []).length ? (
                    printer.tags.map((tag) => <i key={tag}>{tag}</i>)
                  ) : (
                    <i>no tags</i>
                  )}
                </span>
                <span className="fleet-card-footer">
                  {printer.location || "No location"}
                  <i>Open printer</i>
                </span>
              </button>
            );
          })}
        </section>
      ) : (
        <EmptyState
          title={printers.length ? "No printers match these filters." : "Bring your first printer online."}
          copy={printers.length ? "Try a different name, tag, filament, or status." : "Add a printer to start a local fleet record."}
          action={printers.length ? undefined : "Add printer"}
          onAction={printers.length ? undefined : onAdd}
        />
      )}
    </main>
  );
}

function QueuePreview({ data, onAdd }: { data: HubData; onAdd: () => void }) {
  const jobs = data.jobs
    .filter((job) => job.state === "queued" && job.parent_job_id === null)
    .sort((a, b) => (a.queue_position ?? 999) - (b.queue_position ?? 999))
    .slice(0, 4);
  return (
    <div className="queue-table-wrap">
      {jobs.length ? (
        <table className="queue-table">
          <thead>
            <tr>
              <th>Job</th>
              <th>Printer</th>
              <th>Material</th>
              <th>Estimate</th>
            </tr>
          </thead>
          <tbody>
            {jobs.map((job) => (
              <tr key={job.id}>
                <td>
                  <strong>{job.name}</strong>
                  <span>
                    {job.filename} ·{" "}
                    {parentRunStats(job, data.jobs).completed} completed ·{" "}
                    {parentRunStats(job, data.jobs).active} active ·{" "}
                    {parentRunStats(job, data.jobs).total} total
                  </span>
                </td>
                <td>
                  {data.printers.find(
                    (printer) => printer.id === job.target_printer_id,
                  )?.name || "Unassigned"}
                </td>
                <td>{job.material || "Not set"}</td>
                <td>{duration(job.estimated_minutes)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        <EmptyState
          title="No jobs waiting."
          copy="Queue a compatible file when you are ready."
          action="Queue job"
          onAction={onAdd}
        />
      )}
    </div>
  );
}

function QueueView({
  jobs,
  printers,
  files,
  onAdd,
  onMove,
  onDeliver,
  onAutoRoute,
  onEdit,
  onResumeDispatch,
  onDisarmAutomation,
  onComplete,
  onControl,
  onRemove,
}: {
  jobs: Job[];
  printers: Printer[];
  files: FileItem[];
  onAdd: () => void;
  onMove: (job: Job, direction: -1 | 1) => void;
  onDeliver: (job: Job) => void;
  onAutoRoute: (job: Job) => void;
  onEdit: (job: Job) => void;
  onResumeDispatch: (job: Job) => void;
  onDisarmAutomation: (job: Job) => void;
  onComplete: (job: Job) => void;
  onControl: (
    job: Job,
    action: "start" | "pause" | "resume" | "cancel",
  ) => void;
  onRemove: (job: Job) => void;
}) {
  const queue = jobs
    .filter((job) => job.state === "queued" && job.parent_job_id === null)
    .sort((a, b) => (a.queue_position ?? 99999) - (b.queue_position ?? 99999));
  const activeAndRecent = jobs
    .filter((job) => job.state !== "queued")
    .sort((a, b) => b.created_at.localeCompare(a.created_at));
  const confirmControl = (
    job: Job,
    action: "start" | "pause" | "resume" | "cancel",
  ) => {
    if (
      window.confirm(
        `Send ${action.toUpperCase()} to ${job.name}'s assigned printer? This sends a real local-network command.`,
      )
    )
      onControl(job, action);
  };
  const confirmComplete = (job: Job) => {
    if (
      window.confirm(
        `Record ${job.name}${job.run_number ? ` run ${job.run_number}` : ""} as complete? Manual completion never redispatches immediately. You must physically inspect and clear the bed, then confirm READY on the printer.`,
      )
    )
      onComplete(job);
  };
  const confirmResumeDispatch = (job: Job) => {
    const freshUploadWarning = job.override_existing_file
      ? " This job will attempt a fresh file transfer before every automated run instead of reusing its recorded delivery. A printer may reject the same filename; this does not grant overwrite permission."
      : "";
    if (
      window.confirm(
        `Resume dispatch for ${job.name}? Eligible clear printers may claim new child runs after this parent is unpaused.${freshUploadWarning}`,
      )
    )
      onResumeDispatch(job);
  };
  const confirmDisarmAutomation = (job: Job) => {
    if (
      window.confirm(
        `Disarm automation for ${job.name}? This turns off auto-restart and trusted auto-clear, and pauses future dispatch. A physical run already in progress is not stopped; use Cancel separately if needed.`,
      )
    )
      onDisarmAutomation(job);
  };
  return (
    <main className="home-main view-main">
      <section className="view-heading">
        <div>
          <p className="eyebrow">PRODUCTION QUEUE</p>
          <h1>Plan the next print before a machine goes idle.</h1>
          <p className="lede">
            Parent tasks stay queued while immutable child runs track physical
            work. Every printer command asks for final confirmation.
          </p>
        </div>
        <button className="button primary" onClick={onAdd}>
          Queue job
        </button>
      </section>
      <section className="surface-panel queue-full">
        {queue.length ? (
          <table className="queue-table">
            <thead>
              <tr>
                <th>#</th>
                <th>Job</th>
                <th>Printer</th>
                <th>Material</th>
                <th>Routing rules</th>
                <th>Estimate</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {queue.map((job, index) => {
                const stats = parentRunStats(job, jobs);
                const sourceFile =
                  files.find((file) => file.id === job.file_id) ?? null;
                const readyRoutes = printers.filter((printer) =>
                  isJobCompatible(job, printer, sourceFile),
                ).length;
                const effectiveRules = jobRules(job, sourceFile);
                return (
                <tr key={job.id} className={job.dispatch_paused ? "dispatch-paused-row" : ""}>
                  <td>
                    <div className="queue-order">
                      <b>{job.queue_position}</b>
                      <span>
                        <button
                          disabled={!index}
                          onClick={() => onMove(job, -1)}
                        >
                          Up
                        </button>
                        <button
                          disabled={index === queue.length - 1}
                          onClick={() => onMove(job, 1)}
                        >
                          Down
                        </button>
                      </span>
                    </div>
                  </td>
                  <td>
                    <strong>{job.name}</strong>
                    <span>
                      {job.filename} · {job.delivery_state === "uploaded"
                        ? "Uploaded"
                        : job.file_id
                          ? "Ready to upload"
                          : "No file attached"}
                      {" · "}{runPlan(job)}
                      {job.auto_restart ? " · auto-restart armed" : ""}
                      {job.auto_clear ? " · verified auto-clear enabled" : ""}
                    </span>
                    <span className="parent-run-summary">
                      <b>{stats.completed} completed</b>
                      <b>{stats.active} active</b>
                      <b>{stats.total} total</b>
                      {job.dispatch_paused && (
                        <b className="paused">Dispatch paused</b>
                      )}
                    </span>
                  </td>
                  <td>
                    {printers.find(
                      (printer) => printer.id === job.target_printer_id,
                    )?.name || "Unassigned"}
                  </td>
                  <td>{job.material || "Not set"}</td>
                  <td className="routing-cell">
                    <strong>
                      {readyRoutes} ready route{readyRoutes === 1 ? "" : "s"}
                    </strong>
                    <span>
                      {effectiveRules.length
                        ? effectiveRules.join(" · ")
                        : "Any fleet printer"}
                    </span>
                    {job.override_existing_file && (
                      <span className="routing-options">
                        Fresh upload required for each automated run
                      </span>
                    )}
                  </td>
                  <td>{duration(job.estimated_minutes)}</td>
                  <td>
                    <div className="table-actions">
                      {job.dispatch_paused && (
                        <button
                          className="button primary"
                          onClick={() => confirmResumeDispatch(job)}
                        >
                          Resume dispatch
                        </button>
                      )}
                      {(job.auto_restart ||
                        job.auto_clear ||
                        (stats.active > 0 && !job.dispatch_paused)) && (
                        <button
                          className="button quiet"
                          onClick={() => confirmDisarmAutomation(job)}
                        >
                          Disarm automation
                        </button>
                      )}
                      {job.file_id &&
                        job.target_printer_id &&
                        job.delivery_state !== "uploaded" && (
                          <button
                            className="button quiet"
                            onClick={() => onDeliver(job)}
                          >
                            Upload
                          </button>
                        )}
                      {job.delivery_state !== "uploaded" && (
                        <button
                          className="button quiet"
                          onClick={() => onAutoRoute(job)}
                        >
                          Auto-route
                        </button>
                      )}
                      {job.delivery_state === "uploaded" &&
                        job.target_printer_id &&
                        !job.dispatch_paused && (
                        <button
                          className="button primary"
                          onClick={() => confirmControl(job, "start")}
                        >
                          Start
                        </button>
                      )}
                      <button
                        className="button quiet"
                        disabled={stats.active > 0}
                        title={
                          stats.active
                            ? "Wait for active child runs before editing this parent task"
                            : undefined
                        }
                        onClick={() => onEdit(job)}
                      >
                        Edit
                      </button>
                      <button
                        className="button quiet"
                        onClick={() => onRemove(job)}
                      >
                        Remove
                      </button>
                    </div>
                  </td>
                </tr>
                );
              })}
            </tbody>
          </table>
        ) : (
          <EmptyState
            title="No jobs are waiting."
            copy="Add a compatible file to your production queue."
            action="Queue job"
            onAction={onAdd}
          />
        )}
      </section>
      <section className="surface-panel completed-panel">
        <div className="section-heading compact">
          <div>
            <p className="eyebrow">ACTIVE & RECENT</p>
            <h2>Started, completed, and cancelled jobs</h2>
          </div>
        </div>
        {activeAndRecent.length ? (
          <div className="recent-jobs">
            {activeAndRecent.slice(0, 12).map((job) => {
              const parent = job.parent_job_id
                ? jobs.find((item) => item.id === job.parent_job_id)
                : null;
              const stats = job.parent_job_id
                ? null
                : parentRunStats(job, jobs);
              const isChildRun = Boolean(job.parent_job_id);
              return (
              <article key={job.id} className={isChildRun ? "child-run-row" : "parent-history-row"}>
                <div>
                  <strong>
                    {isChildRun
                      ? `${parent?.name ?? job.name} — Run ${job.run_number ?? "?"}`
                      : job.name}
                  </strong>
                  <span>
                    {printers.find(
                      (printer) => printer.id === job.target_printer_id,
                    )?.name || "No printer"}{" "}
                    · {duration(job.estimated_minutes)}
                    {stats
                      ? ` · ${stats.completed} completed · ${stats.active} active · ${stats.total} total`
                      : " · child run"}
                  </span>
                </div>
                <span className={statusClass(job.state)}>
                  {labels[job.state]}
                </span>
                {isChildRun && job.state === "running" && (
                  <button
                    className="button quiet"
                    onClick={() => confirmControl(job, "pause")}
                  >
                    Pause
                  </button>
                )}
                {isChildRun && job.state === "paused" && (
                  <button
                    className="button primary"
                    onClick={() => confirmControl(job, "resume")}
                  >
                    Resume
                  </button>
                )}
                {isChildRun && (job.state === "running" || job.state === "paused") && (
                  <button className="button quiet" onClick={() => confirmComplete(job)}>
                    Complete
                  </button>
                )}
                {isChildRun && (job.state === "running" || job.state === "paused") && (
                  <button
                    className="button danger-button"
                    onClick={() => confirmControl(job, "cancel")}
                  >
                    Cancel
                  </button>
                )}
              </article>
              );
            })}
          </div>
        ) : (
          <p className="panel-footer">
            Jobs remain local until you explicitly upload them to a compatible
            connected printer.
          </p>
        )}
      </section>
    </main>
  );
}

function MaterialsView({
  materials,
  printers,
  onAdd,
  onRemove,
}: {
  materials: Material[];
  printers: Printer[];
  onAdd: () => void;
  onRemove: (material: Material) => void;
}) {
  return (
    <main className="home-main view-main">
      <section className="view-heading">
        <div>
          <p className="eyebrow">MATERIAL SHELF</p>
          <h1>Know what is loaded and what needs restocking.</h1>
          <p className="lede">
            Spool records stay in your local workspace. Inventory is never sent
            to a cloud account.
          </p>
        </div>
        <button className="button primary" onClick={onAdd}>
          Add spool
        </button>
      </section>
      <section className="material-grid">
        {materials.length ? (
          materials.map((material) => (
            <article className="material-card" key={material.id}>
              <div className="material-card-head">
                <span
                  className="spool-swatch"
                  style={{ borderColor: material.color }}
                >
                  <i style={{ background: material.color }} />
                </span>
                <b>{material.remaining_percent}%</b>
              </div>
              <p className="eyebrow">{material.material_type}</p>
              <h2>{material.name}</h2>
              <p>
                {material.brand || "Unbranded"} · {material.spool_grams}g spool
              </p>
              <div className="material-bar">
                <i
                  style={{
                    width: `${material.remaining_percent}%`,
                    background: material.color,
                  }}
                />
              </div>
              <small>
                {printers.find(
                  (printer) => printer.id === material.assigned_printer_id,
                )?.name || "On shelf"}
              </small>
              <button
                className="text-button remove-link"
                onClick={() => onRemove(material)}
              >
                Remove
              </button>
            </article>
          ))
        ) : (
          <EmptyState
            title="Your material shelf is empty."
            copy="Add your first spool to track stock before a job reaches the bed."
            action="Add spool"
            onAction={onAdd}
          />
        )}
      </section>
    </main>
  );
}

function FilesView({
  files,
  printers,
  onAdd,
  onQueue,
  onRemove,
}: {
  files: FileItem[];
  printers: Printer[];
  onAdd: () => void;
  onQueue: (file: FileItem) => void;
  onRemove: (file: FileItem) => void;
}) {
  return (
    <main className="home-main view-main">
      <section className="view-heading">
        <div>
          <p className="eyebrow">FILE LIBRARY</p>
          <h1>Store print-ready files once, then queue them anywhere.</h1>
          <p className="lede">
            Files stay on this computer until you explicitly upload one to a
            compatible local printer.
          </p>
        </div>
        <button className="button primary" onClick={onAdd}>
          Upload file
        </button>
      </section>
      <section className="file-grid">
        {files.length ? (
          files.map((file) => (
            <article className="file-card" key={file.id}>
              <span className="file-type">{file.file_type.toUpperCase()}</span>
              {file.auto_eject_verified && (
                <span className="file-ready">Auto-eject verified</span>
              )}
              <h2>{file.name}</h2>
              <p>{file.filename}</p>
              <dl>
                <div>
                  <dt>Material</dt>
                  <dd>{file.material || "Not set"}</dd>
                </div>
                <div>
                  <dt>Estimate</dt>
                  <dd>{duration(file.estimated_minutes)}</dd>
                </div>
                <div>
                  <dt>Local copy</dt>
                  <dd>{fileSize(file.size_bytes)}</dd>
                </div>
                <div>
                  <dt>Filament est.</dt>
                  <dd>{file.estimated_filament_grams === null ? "Not found" : `${file.estimated_filament_grams}g`}</dd>
                </div>
              </dl>
              <div className="compatibility-block">
                <span>
                  Profile rules ·{" "}
                  {printers.filter((printer) =>
                    isFileCompatible(file, printer),
                  ).length}{" "}
                  fleet match
                  {printers.filter((printer) =>
                    isFileCompatible(file, printer),
                  ).length === 1
                    ? ""
                    : "es"}
                </span>
                <div className="rule-chips">
                  <i>
                    {file.printer_manufacturer
                      ? `Make ${file.printer_manufacturer}`
                      : "Any make"}
                  </i>
                  <i>
                    {file.printer_model
                      ? `Model ${file.printer_model}`
                      : "Any model"}
                  </i>
                  <i>
                    {file.nozzle_diameter
                      ? `${file.nozzle_diameter} mm nozzle`
                      : "Any nozzle"}
                  </i>
                  {(file.filament_profiles ?? []).length ? (
                    file.filament_profiles.map((profile) => (
                      <i key={profile}>{profile}</i>
                    ))
                  ) : (
                    <i>Any filament</i>
                  )}
                </div>
              </div>
              {file.notes && <small>{file.notes}</small>}
              <div className="file-actions">
                <button
                  className="button primary"
                  onClick={() => onQueue(file)}
                >
                  Queue file
                </button>
                <button className="button quiet" onClick={() => onRemove(file)}>
                  Remove
                </button>
              </div>
            </article>
          ))
        ) : (
          <EmptyState
            title="Your file library is empty."
            copy="Upload a print-ready file to reuse it across compatible printers."
            action="Upload file"
            onAction={onAdd}
          />
        )}
      </section>
    </main>
  );
}

function IntegrationsView({
  serviceMode,
}: {
  serviceMode: "preview" | "local" | "offline";
}) {
  const endpoints = [
    {
      group: "Workspace",
      methods: "GET",
      path: "/api/dashboard",
      detail: "Fleet, queue, materials, files, and recent activity.",
    },
    {
      group: "Printers",
      methods: "POST · PATCH · DELETE",
      path: "/api/printers",
      detail: "Create and maintain local printer records and compatibility.",
    },
    {
      group: "Jobs",
      methods: "POST · PATCH · DELETE",
      path: "/api/jobs",
      detail: "Queue work, set routing rules, repetitions, and automation.",
    },
    {
      group: "Route candidates",
      methods: "GET",
      path: "/api/jobs/{id}/route-candidates",
      detail: "Preview compatible machines without changing assignment.",
    },
    {
      group: "Confirm route",
      methods: "POST",
      path: "/api/jobs/{id}/route",
      detail: "Assign one reviewed compatible printer to the job.",
    },
    {
      group: "Files",
      methods: "POST · GET · DELETE",
      path: "/api/files",
      detail: "Upload, download, catalog, and remove local print files.",
    },
    {
      group: "Materials",
      methods: "POST · PATCH · DELETE",
      path: "/api/materials",
      detail: "Track spools and their current printer assignments.",
    },
    {
      group: "Adapters",
      methods: "GET · POST",
      path: "/api/adapters",
      detail: "Inspect adapter support and verify local printer connections.",
    },
    {
      group: "Commands",
      methods: "POST",
      path: "/api/jobs/{id}/control",
      detail: "Confirmed start, pause, resume, and cancel commands.",
    },
    {
      group: "Automation API",
      methods: "GET · POST",
      path: "/api/public/v1/*",
      detail:
        "Stable external API. Requires a Bearer token when MIRDAIN_API_TOKEN is configured.",
    },
  ];
  return (
    <main className="home-main view-main">
      <section className="view-heading integration-heading">
        <div>
          <p className="eyebrow">INTEGRATIONS & LOCAL API</p>
          <h1>Connect your own tools without sending farm data to a cloud.</h1>
          <p className="lede">
            The same local REST interface used by this dashboard is available
            for scripts, shop displays, schedulers, and home automation.
          </p>
        </div>
        <span className={`api-state ${serviceMode}`}>
          <i />
          {serviceMode === "local"
            ? "API reachable"
            : serviceMode === "offline"
              ? "API disconnected"
              : "Start local service"}
        </span>
      </section>
      <section className="integration-callout">
        <div>
          <p className="eyebrow">BASE URL</p>
          <code>{window.location.origin}/api</code>
          <span>
            Interactive OpenAPI documentation is generated by the local
            service.
          </span>
        </div>
        <a className="button primary" href="/docs" target="_blank" rel="noreferrer">
          Open API docs
        </a>
      </section>
      <section className="simulator-callout">
        <span aria-hidden="true">SIM</span>
        <div>
          <p className="eyebrow">FIRST-RUN ADAPTER</p>
          <h2>Explore the full workflow with no printer attached.</h2>
          <p>
            Add a printer with <code>adapter_type: "simulator"</code> to test
            telemetry, routing, delivery, and confirmed controls safely before
            connecting hardware.
          </p>
        </div>
      </section>
      <section className="endpoint-grid" aria-label="REST endpoint summary">
        {endpoints.map((endpoint) => (
          <article className="endpoint-card" key={`${endpoint.methods}-${endpoint.path}`}>
            <span>{endpoint.group}</span>
            <strong>{endpoint.methods}</strong>
            <code>{endpoint.path}</code>
            <p>{endpoint.detail}</p>
          </article>
        ))}
      </section>
      <section className="safety-grid">
        <article className="safety-card">
          <p className="eyebrow">NETWORK BOUNDARY</p>
          <h2>Keep the service private</h2>
          <p>
            Keep dashboard routes on loopback or a trusted VPN. On a shop LAN,
            use host firewall rules and never forward the service port directly
            from your router to the public internet.
          </p>
        </article>
        <article className="safety-card">
          <p className="eyebrow">REMOTE ACCESS</p>
          <h2>Use a trusted VPN</h2>
          <p>
            Reach the farm through a private VPN such as WireGuard or Tailscale
            with device access controls. For <code>/api/public/v1</code>, set
            <code> MIRDAIN_API_TOKEN</code> and send it as a Bearer token.
            Treat anyone with access as able to control physical equipment.
          </p>
        </article>
        <article className="safety-card warning-card">
          <p className="eyebrow">PHYSICAL SAFETY</p>
          <h2>Commands require supervision</h2>
          <p>
            Test adapters, ejection routines, and material changes locally.
            Keep automatic starts disabled until the bed is clear and your
            workflow has been verified on the exact machine and file.
          </p>
        </article>
      </section>
    </main>
  );
}

function PrinterView({
  printer,
  jobs,
  onBack,
  onUpdate,
  onArmAutomation,
  onRemove,
  onAddJob,
  onConnect,
  onRefresh,
  onReady,
}: {
  printer: Printer;
  jobs: Job[];
  onBack: () => void;
  onUpdate: (payload: Partial<Printer>, message: string) => void;
  onArmAutomation: () => void;
  onRemove: () => void;
  onAddJob: () => void;
  onConnect: () => void;
  onRefresh: () => void;
  onReady: () => void;
}) {
  const printerQueue = jobs
    .filter(
      (job) =>
        job.target_printer_id === printer.id &&
        job.state === "queued" &&
        job.parent_job_id === null,
    )
    .sort((a, b) => (a.queue_position ?? 999) - (b.queue_position ?? 999));
  const servicePercent = Math.min(
    100,
    Math.round((printer.run_hours / printer.maintenance_due_hours) * 100),
  );
  const cameraAvailable = Boolean(printer.camera_url);
  const unattendedStartSupported = supportsUnattendedStart(printer);
  const needsClearance = ["awaiting_confirmation", "unknown"].includes(
    printer.clearance_state ?? "unknown",
  );
  const readyNeedsConnection =
    !["manual", "bambu_lan"].includes(printer.adapter_type) &&
    printer.connection_state !== "connected";
  const canArmAutomation =
    unattendedStartSupported &&
    printer.connection_state === "connected" &&
    printer.state === "ready" &&
    ["confirmed", "auto_verified"].includes(printer.clearance_state);
  const confirmReady = () => {
    if (
      window.confirm(
        `Confirm READY for ${printer.name}? Only continue after physically inspecting the build area, removing the completed or failed print, and verifying the bed is clear.`,
      )
    )
      onReady();
  };
  return (
    <main className="printer-page">
      <button className="back-link" onClick={onBack}>
        &lt;- Back to fleet
      </button>
      <section className="printer-page-title">
        <div>
          <p className="eyebrow">LOCAL PRINTER</p>
          <h1>{printer.name}</h1>
          <p>
            {printer.manufacturer} / {printer.model} <span>·</span>{" "}
            {printer.nozzle_diameter ?? 0.4} mm nozzle <span>·</span>{" "}
            {filamentSystemLabel(printer.filament_system)} <span>·</span>{" "}
            {printer.location || printer.host || "No location set"}
          </p>
        </div>
        <div className="title-status">
          <span className={statusClass(printer.state)}>
            {labels[printer.state]}
          </span>
          <span
            className={`clearance-pill ${printer.clearance_state ?? "unknown"}`}
          >
            {clearanceLabel(printer.clearance_state)}
          </span>
          <span className="last-seen">
            Seen {relativeTime(printer.last_seen_at)}
          </span>
        </div>
      </section>
      {needsClearance && (
        <section className="clearance-alert" role="alert">
          <div>
            <p className="eyebrow">PHYSICAL CLEARANCE REQUIRED</p>
            <h2>{clearanceLabel(printer.clearance_state)}</h2>
            <p>
              {printer.clearance_state === "awaiting_confirmation"
                ? "A run completed, failed, or was cancelled. Remove the part and debris, inspect the build surface, then explicitly confirm READY."
                : readyNeedsConnection
                  ? "Monitoring is not connected. Reconnect or successfully refresh this printer before confirming the physical bed is clear."
                  : "The service has no trusted evidence that this bed is clear. Inspect the physical printer before allowing any dispatch."}
            </p>
          </div>
          <button
            className="button primary"
            onClick={confirmReady}
            disabled={readyNeedsConnection}
            title={
              readyNeedsConnection
                ? "Reconnect or refresh successfully before confirming READY"
                : undefined
            }
          >
            Confirm bed clear · READY
          </button>
        </section>
      )}
      <section className="printer-metrics">
        <Metric
          label="Active job"
          value={printer.active_job ? 1 : 0}
          detail={printer.active_job || "No active job"}
          tone={printer.state === "printing" ? "amber" : undefined}
        />
        <Metric
          label="Completion"
          value={printer.percent_complete || 0}
          detail={
            printer.percent_complete !== null
              ? "manual or adapter reported"
              : "waiting for telemetry"
          }
        />
        <Metric
          label="Queued here"
          value={printerQueue.length}
          detail={
            printerQueue.length ? "ready after current job" : "no job assigned"
          }
        />
        <Metric
          label="Service remaining"
          value={Math.max(0, printer.maintenance_due_hours - printer.run_hours)}
          detail={`of ${printer.maintenance_due_hours}h interval`}
        />
      </section>
      <section className="printer-page-main">
        <section className="camera-panel">
          <div className="camera-heading">
            <div>
              <p className="eyebrow">LOCAL CONNECTION</p>
              <h2>Telemetry & camera access</h2>
            </div>
            <span className={`camera-status ${cameraAvailable ? "connected" : ""}`}>
              {cameraAvailable ? "CAMERA LIVE" : "NO CAMERA SOURCE"}
            </span>
          </div>
          {cameraAvailable ? (
            <div className="camera-live">
              <img src={printer.camera_url} alt={`Live camera from ${printer.name}`} />
              <span>Direct local camera source</span>
            </div>
          ) : (
            <p className="camera-empty">
              Add a local MJPEG stream or snapshot URL below to show this printer's actual camera feed. Mirdain does not generate a fake live view.
            </p>
          )}
          <div className="connection-card">
            <strong>
              {printer.connection_state === "connected"
                ? `${printer.adapter_type} monitoring active`
                : printer.connection_state === "error"
                  ? "Connection needs attention"
                  : "Connect this printer"}
            </strong>
            <p>
              {printer.connection_message ||
                "Verify a local connection before enabling telemetry, file delivery, or confirmed controls. Credentials use the operating system vault when available, with encrypted local storage as a fallback, and are not written into this database."}
            </p>
            <dl>
              <div>
                <dt>Local host</dt>
                <dd>{printer.host || "Not configured"}</dd>
              </div>
              <div>
                <dt>Bed / nozzle</dt>
                <dd>
                  {printer.bed_temp ?? "-"}° / {printer.nozzle_temp ?? "-"}°
                </dd>
              </div>
              <div>
                <dt>Capabilities</dt>
                <dd>{printer.capabilities.length ? printer.capabilities.join(", ") : "Not connected"}</dd>
              </div>
              <div>
                <dt>Loaded filaments</dt>
                <dd>
                  {(printer.loaded_filaments ?? []).join(", ") ||
                    printer.material ||
                    "Not loaded"}
                </dd>
              </div>
              <div>
                <dt>Bed clearance</dt>
                <dd>{clearanceLabel(printer.clearance_state)}</dd>
              </div>
              <div>
                <dt>Last local sync</dt>
                <dd>{relativeTime(printer.last_refreshed_at)}</dd>
              </div>
            </dl>
            <div className="connection-actions">
              <button className="button primary" onClick={onConnect}>
                {printer.connection_state === "connected"
                  ? "Update connection"
                  : "Connect printer"}
              </button>
              {printer.connection_state === "connected" && (
                <button className="button quiet" onClick={onRefresh}>
                  Refresh now
                </button>
              )}
            </div>
          </div>
          <section className="printer-edit">
            <div>
              <p className="eyebrow">MANUAL UPDATE</p>
              <h2>Keep the local record current</h2>
            </div>
            <div className="quick-status">
              {(
                [
                  "setup",
                  "ready",
                  "printing",
                  "attention",
                  "offline",
                ] as PrinterState[]
              ).map((state) => (
                <button
                  key={state}
                  className={printer.state === state ? "active" : ""}
                  disabled={
                    state === "ready" &&
                    (printer.clearance_state === "occupied" ||
                      readyNeedsConnection)
                  }
                  title={
                    state === "ready" &&
                    printer.clearance_state === "occupied"
                      ? "An active run currently occupies this bed"
                      : state === "ready" && readyNeedsConnection
                        ? "Reconnect or refresh successfully before confirming READY"
                      : state === "ready" && needsClearance
                      ? "Requires physical inspection and confirmed READY"
                      : undefined
                  }
                  onClick={() => {
                    if (state === "ready" && needsClearance) {
                      confirmReady();
                      return;
                    }
                    onUpdate(
                      {
                        state,
                        percent_complete:
                          state === "printing"
                            ? (printer.percent_complete ?? 0)
                            : null,
                      },
                      `${printer.name} is now ${labels[state].toLowerCase()}.`,
                    );
                  }}
                >
                  {labels[state]}
                </button>
              ))}
            </div>
            <div className="edit-fields">
              <label className="wide-field">
                Camera stream or snapshot URL
                <input
                  type="url"
                  defaultValue={printer.camera_url}
                  placeholder="http://printer.local/webcam/?action=stream"
                  onBlur={(event) =>
                    onUpdate(
                      { camera_url: event.target.value.trim() },
                      `${printer.name} camera source was updated.`,
                    )
                  }
                />
              </label>
              <label>
                Progress %
                <input
                  type="number"
                  min="0"
                  max="100"
                  defaultValue={printer.percent_complete ?? ""}
                  onBlur={(event) =>
                    onUpdate(
                      {
                        percent_complete: event.target.value
                          ? Number(event.target.value)
                          : null,
                      },
                      `${printer.name} progress was updated.`,
                    )
                  }
                />
              </label>
              <label>
                Run hours
                <input
                  type="number"
                  min="0"
                  defaultValue={printer.run_hours}
                  onBlur={(event) =>
                    onUpdate(
                      { run_hours: Number(event.target.value) || 0 },
                      `${printer.name} service hours were updated.`,
                    )
                  }
                />
              </label>
              <label>
                Nozzle diameter (mm)
                <input
                  type="number"
                  min="0.1"
                  max="2"
                  step="0.05"
                  defaultValue={printer.nozzle_diameter ?? 0.4}
                  onBlur={(event) =>
                    onUpdate(
                      {
                        nozzle_diameter:
                          Number(event.target.value) || 0.4,
                      },
                      `${printer.name} nozzle profile was updated.`,
                    )
                  }
                />
              </label>
              <label>
                Filament system
                <select
                  value={printer.filament_system ?? "single"}
                  onChange={(event) =>
                    onUpdate(
                      {
                        filament_system: event.target
                          .value as Printer["filament_system"],
                      },
                      `${printer.name} filament system was updated.`,
                    )
                  }
                >
                  <option value="single">Single spool</option>
                  <option value="ams">AMS</option>
                  <option value="multi">Multi-material</option>
                  <option value="external">External feeder</option>
                </select>
              </label>
              <label className="wide-field">
                Loaded filaments
                <input
                  defaultValue={(printer.loaded_filaments ?? []).join(", ")}
                  placeholder="PETG - Black, PLA - White"
                  onBlur={(event) =>
                    onUpdate(
                      { loaded_filaments: commaList(event.target.value) },
                      `${printer.name} loaded filament slots were updated.`,
                    )
                  }
                />
              </label>
              <label className="wide-field">
                Routing tags
                <input
                  defaultValue={(printer.tags ?? []).join(", ")}
                  placeholder="enclosed, production, flexibles"
                  onBlur={(event) =>
                    onUpdate(
                      { tags: commaList(event.target.value) },
                      `${printer.name} routing tags were updated.`,
                    )
                  }
                />
              </label>
            </div>
          </section>
        </section>
        <aside className="printer-side">
          <section className="side-panel controls-panel">
            <p className="eyebrow">JOB CONTROLS</p>
            <h2>Queue a verified job</h2>
            <p>
              {!unattendedStartSupported
                ? "Unattended START is not available for this adapter. OctoPrint completion polling is too ambiguous for safe automatic dispatch; confirmed manual controls remain available."
                : printer.auto_start_enabled
                ? "Verified auto-start is armed. The local worker checks connected printers every 10 seconds, but only a queued job with a verified ejection file can be reserved."
                : "Jobs stay manual by default. Enable auto-start only after you have tested a reliable ejection workflow."}
            </p>
            <div className="control-actions">
              <button className="button primary" onClick={onAddJob}>
                Add job
              </button>
              <button
                className="button quiet"
                onClick={() =>
                  onUpdate(
                    { favorite: !printer.favorite },
                    `${printer.name} ${printer.favorite ? "was removed from" : "was added to"} favorites.`,
                  )
                }
              >
                {printer.favorite ? "Unfavorite" : "Favorite"}
              </button>
            </div>
            <button
              className="button quiet automation-toggle"
              disabled={
                !printer.auto_start_enabled && !canArmAutomation
              }
              onClick={() => {
                if (printer.auto_start_enabled) {
                  onUpdate(
                    { auto_start_enabled: false },
                    `${printer.name} verified auto-start was disabled.`,
                  );
                  return;
                }
                if (
                  !window.confirm(
                    `Arm verified auto-start for ${printer.name}? Confirm that this exact printer uses a trusted static or DHCP-reserved LAN address, is not registered through another URL or record, the bed is clear, and the ejection workflow has been tested on this machine.`,
                  )
                )
                  return;
                onArmAutomation();
              }}
            >
              {printer.auto_start_enabled
                ? "Disable verified auto-start"
                : unattendedStartSupported
                  ? "Enable verified auto-start"
                  : "Unattended START unavailable"}
            </button>
          </section>
          <section className="side-panel">
            <div className="side-heading">
              <div>
                <p className="eyebrow">PRINTER QUEUE</p>
                <h2>
                  {printerQueue.length
                    ? `${printerQueue.length} waiting`
                    : "Nothing waiting"}
                </h2>
              </div>
            </div>
            {printerQueue.length ? (
              <div className="mini-queue">
                {printerQueue.map((job) => (
                  <div key={job.id}>
                    <span>{job.queue_position}</span>
                    <p>
                      <strong>{job.name}</strong>
                      <small>
                        {duration(job.estimated_minutes)} /{" "}
                        {job.material || "Material not set"}
                      </small>
                      <small>
                        {parentRunStats(job, jobs).completed} completed ·{" "}
                        {parentRunStats(job, jobs).active} active ·{" "}
                        {parentRunStats(job, jobs).total} total
                      </small>
                    </p>
                  </div>
                ))}
              </div>
            ) : (
              <p className="quiet-copy">
                Jobs assigned to this printer will wait here.
              </p>
            )}
          </section>
          <section className="side-panel">
            <p className="eyebrow">ROUTING PROFILE</p>
            <h2>
              {printer.nozzle_diameter ?? 0.4} mm ·{" "}
              {filamentSystemLabel(printer.filament_system)}
            </h2>
            <div className="tag-list detail-tags">
              {(printer.tags ?? []).length ? (
                printer.tags.map((tag) => <i key={tag}>{tag}</i>)
              ) : (
                <i>no restrictions</i>
              )}
            </div>
            <p className="quiet-copy">
              {(printer.loaded_filaments ?? []).length
                ? `Loaded: ${printer.loaded_filaments.join(", ")}`
                : "No loaded filament slots have been recorded."}
            </p>
          </section>
          <section className="side-panel">
            <p className="eyebrow">MAINTENANCE</p>
            <h2>
              {Math.max(0, printer.maintenance_due_hours - printer.run_hours)}h
              until service
            </h2>
            <div className="maintenance-bar">
              <i style={{ width: `${servicePercent}%` }} />
            </div>
            <p className="quiet-copy">
              {printer.note ||
                "Add nozzle, build plate, or setup notes when needed."}
            </p>
          </section>
          <button className="button danger-button" onClick={onRemove}>
            Remove printer
          </button>
        </aside>
      </section>
    </main>
  );
}

function Metric({
  label,
  value,
  detail,
  tone,
}: {
  label: string;
  value: number;
  detail: string;
  tone?: "amber" | "warning";
}) {
  return (
    <article className={`metric ${tone ?? ""}`}>
      <span>{label}</span>
      <strong>{value}</strong>
      <small>{detail}</small>
    </article>
  );
}
function EmptyState({
  title,
  copy,
  action,
  onAction,
}: {
  title: string;
  copy: string;
  action?: string;
  onAction?: () => void;
}) {
  return (
    <div className="empty-state">
      <strong>{title}</strong>
      <span>{copy}</span>
      {action && (
        <button className="button quiet" onClick={onAction}>
          {action}
        </button>
      )}
    </div>
  );
}

function RouteReviewModal({
  job,
  candidates,
  onClose,
  onConfirm,
}: {
  job: Job;
  candidates: RouteCandidate[];
  onClose: () => void;
  onConfirm: (printerId: string) => void;
}) {
  const [printerId, setPrinterId] = useState(candidates[0]?.printer_id ?? "");
  return (
    <Modal
      eyebrow="ROUTING REVIEW"
      title={`Choose where ${job.name} should run`}
      copy="Mirdain checked only ready, connected printers with file delivery and the required material profile. Confirm a route before changing the current assignment."
      onClose={onClose}
    >
      <form
        onSubmit={(event) => {
          event.preventDefault();
          onConfirm(printerId);
        }}
      >
        <div className="route-options" role="radiogroup" aria-label="Compatible printers">
          {candidates.map((candidate) => (
            <button
              type="button"
              key={candidate.printer_id}
              className={candidate.printer_id === printerId ? "route-option selected" : "route-option"}
              role="radio"
              aria-checked={candidate.printer_id === printerId}
              onClick={() => setPrinterId(candidate.printer_id)}
            >
              <span>
                <strong>{candidate.printer_name}</strong>
                <small>{candidate.reason}</small>
              </span>
              <b>{candidate.score}</b>
            </button>
          ))}
        </div>
        {actions(onClose, "Confirm route")}
      </form>
    </Modal>
  );
}

function Modal({
  eyebrow,
  title,
  copy,
  children,
  onClose,
}: {
  eyebrow: string;
  title: string;
  copy: string;
  children: React.ReactNode;
  onClose: () => void;
}) {
  return (
    <div className="modal-backdrop" role="presentation">
      <section
        className="modal"
        role="dialog"
        aria-modal="true"
        aria-label={title}
      >
        <div className="modal-heading">
          <div>
            <p className="eyebrow">{eyebrow}</p>
            <h2>{title}</h2>
          </div>
          <button className="close" aria-label="Close" onClick={onClose}>
            x
          </button>
        </div>
        <p className="modal-copy">{copy}</p>
        {children}
      </section>
    </div>
  );
}
function actions(onClose: () => void, label: string) {
  return (
    <div className="modal-actions">
      <button className="button quiet" type="button" onClick={onClose}>
        Cancel
      </button>
      <button className="button primary" type="submit">
        {label}
      </button>
    </div>
  );
}

function PrinterModal({
  onClose,
  onSubmit,
}: {
  onClose: () => void;
  onSubmit: (payload: Record<string, unknown>) => void;
}) {
  const [adapterType, setAdapterType] = useState("manual");
  const unattendedStartSupported = [
    "moonraker",
    "prusalink",
    "simulator",
  ].includes(adapterType);
  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    onSubmit({
      name: String(form.get("name")),
      manufacturer: String(form.get("manufacturer")),
      model: String(form.get("model")),
      adapter_type: adapterType,
      host: String(form.get("host")),
      camera_url: String(form.get("camera_url")).trim(),
      location: String(form.get("location")),
      material: String(form.get("material")),
      supported_materials: materialList(String(form.get("supported_materials"))),
      auto_start_enabled:
        false,
      maintenance_due_hours: Number(form.get("maintenance_due_hours")) || 250,
      nozzle_diameter: Number(form.get("nozzle_diameter")) || 0.4,
      tags: commaList(String(form.get("tags"))),
      filament_system: String(form.get("filament_system")),
      loaded_filaments: commaList(String(form.get("loaded_filaments"))),
    });
  };
  return (
    <Modal
      eyebrow="LOCAL ONBOARDING"
      title="Add a printer"
      copy="This saves an equipment record locally. Network credentials and hardware commands stay disabled until you choose a verified adapter."
      onClose={onClose}
    >
      <form onSubmit={submit}>
        <label>
          Printer name
          <input name="name" placeholder="e.g. Forge North" required />
        </label>
        <div className="form-split">
          <label>
            Manufacturer
            <input name="manufacturer" placeholder="e.g. Bambu Lab" required />
          </label>
          <label>
            Model
            <input name="model" placeholder="e.g. P1S" required />
          </label>
        </div>
        <div className="form-split">
          <label>
            Connection
            <select
              name="adapter_type"
              value={adapterType}
              onChange={(event) => setAdapterType(event.target.value)}
            >
              <option value="manual">Manual tracking</option>
              <option value="simulator">Simulator (no hardware)</option>
              <option value="bambu_lan">
                Bambu printer (record only — LAN control intentionally disabled)
              </option>
              <option value="prusalink">PrusaLink</option>
              <option value="octoprint">OctoPrint</option>
              <option value="moonraker">Moonraker</option>
            </select>
          </label>
          <label>
            Local host
            <input name="host" placeholder="192.168.1.88" />
          </label>
        </div>
        <label>
          Camera stream or snapshot URL (optional)
          <input name="camera_url" type="url" placeholder="http://printer.local/webcam/?action=stream" />
        </label>
        <div className="form-split">
          <label>
            Location
            <input name="location" placeholder="e.g. Shop floor" />
          </label>
          <label>
            Service interval (hours)
            <input
              name="maintenance_due_hours"
              type="number"
              min="1"
              defaultValue="250"
            />
          </label>
        </div>
        <label>
          Loaded material
          <input name="material" placeholder="e.g. PETG - Black" />
        </label>
        <label>
          Compatible materials
          <input
            name="supported_materials"
            placeholder="e.g. PLA, PETG, ABS"
          />
        </label>
        <div className="form-split">
          <label>
            Nozzle diameter (mm)
            <input
              name="nozzle_diameter"
              type="number"
              min="0.1"
              max="2"
              step="0.05"
              defaultValue="0.4"
            />
          </label>
          <label>
            Filament system
            <select name="filament_system" defaultValue="single">
              <option value="single">Single spool</option>
              <option value="ams">AMS</option>
              <option value="multi">Multi-material</option>
              <option value="external">External feeder</option>
            </select>
          </label>
        </div>
        <label>
          Loaded filaments
          <input
            name="loaded_filaments"
            placeholder="e.g. PETG - Black, PLA - White"
          />
          <small className="field-help">
            Enter one slot or profile per comma. Order can mirror the feeder.
          </small>
        </label>
        <label>
          Routing tags
          <input
            name="tags"
            placeholder="e.g. enclosed, production, flexibles"
          />
          <small className="field-help">
            Jobs can require all, any, or exactly this set of tags.
          </small>
        </label>
        <div className="check-row">
          <span>
            <strong>Automatic starts begin disarmed</strong>
            <small>
              {unattendedStartSupported
                ? "Connect the printer, confirm READY, then arm it after confirming a trusted static or DHCP-reserved address and that the machine is not registered through another URL or record."
                : "Available for Moonraker, PrusaLink, and the simulator. OctoPrint remains confirmed-manual because polling cannot prove safe completion."}
            </small>
          </span>
        </div>
        {actions(onClose, "Save locally")}
      </form>
    </Modal>
  );
}
function ConnectionModal({
  printer,
  onClose,
  onSubmit,
}: {
  printer: Printer;
  onClose: () => void;
  onSubmit: (payload: Record<string, unknown>) => void;
}) {
  const [adapterType, setAdapterType] = useState(
    printer.adapter_type === "manual" ? "octoprint" : printer.adapter_type,
  );
  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const optionalSecret = (name: string) => {
      const value = form.get(name);
      return typeof value === "string" && value ? value : null;
    };
    onSubmit({
      adapter_type: adapterType,
      host: String(form.get("host")),
      api_key: optionalSecret("api_key"),
      username: optionalSecret("username"),
      password: optionalSecret("password"),
    });
  };
  return (
    <Modal
      eyebrow="LOCAL CONNECTION"
      title={`Connect ${printer.name}`}
      copy="This verifies local telemetry on your home or shop network. It does not upload a file or issue a printer command. Credentials use the operating system vault when available, with encrypted local storage as a fallback, and are never written into the hub database."
      onClose={onClose}
    >
      <form onSubmit={submit}>
        <label>
          Connection type
          <select
            name="adapter_type"
            value={adapterType}
            onChange={(event) => setAdapterType(event.target.value)}
          >
            <option value="octoprint">OctoPrint</option>
            <option value="moonraker">Moonraker / Klipper</option>
            <option value="prusalink">PrusaLink</option>
            <option value="simulator">Simulator (no hardware)</option>
            <option value="bambu_lan" disabled>
              Bambu LAN (intentionally unsupported)
            </option>
          </select>
        </label>
        <label>
          Local IP address or URL
          <input
            name="host"
            defaultValue={printer.host}
            placeholder="e.g. 192.168.1.88 or http://printer.local"
            required
          />
        </label>
        {adapterType === "prusalink" ? (
          <div className="form-split">
            <label>
              Digest auth username (optional)
              <input
                name="username"
                autoComplete="username"
                placeholder="Leave blank to reuse saved username"
              />
            </label>
            <label>
              Digest auth password (optional)
              <input
                name="password"
                type="password"
                autoComplete="current-password"
                placeholder="Leave blank to reuse saved password"
              />
            </label>
          </div>
        ) : adapterType !== "simulator" ? (
          <label>
            API key
            <input
              name="api_key"
              type="password"
              placeholder="Leave blank to use the key already saved for this printer"
              autoComplete="off"
            />
          </label>
        ) : null}
        <p className="form-help">
          {adapterType === "simulator"
            ? "Simulator mode creates predictable telemetry and command responses without contacting a physical printer. It is the safest first-run path."
            : adapterType === "prusalink"
              ? "PrusaLink uses HTTP Digest authentication. No print is started, paused, cancelled, or uploaded during this connection test."
              : "OctoPrint and Moonraker enable file delivery and confirmed controls after this status test. No print is started, paused, cancelled, or uploaded during this step."}
        </p>
        {actions(onClose, "Test and save connection")}
      </form>
    </Modal>
  );
}
function JobModal({
  printers,
  files,
  seed,
  editing,
  onClose,
  onSubmit,
}: {
  printers: Printer[];
  files: FileItem[];
  seed: Partial<Job> | null;
  editing: boolean;
  onClose: () => void;
  onSubmit: (payload: Record<string, unknown>) => void;
}) {
  const [fileId, setFileId] = useState(seed?.file_id ?? "");
  const [filename, setFilename] = useState(seed?.filename ?? "");
  const [material, setMaterial] = useState(seed?.material ?? "");
  const [targetPrinterId, setTargetPrinterId] = useState(
    seed?.target_printer_id ?? "",
  );
  const [requiredManufacturer, setRequiredManufacturer] = useState(
    seed?.required_printer_manufacturer ?? "",
  );
  const [requiredModel, setRequiredModel] = useState(
    seed?.required_printer_model ?? "",
  );
  const [requiredNozzle, setRequiredNozzle] = useState(
    seed?.required_nozzle_diameter
      ? String(seed.required_nozzle_diameter)
      : "",
  );
  const [requiredFilaments, setRequiredFilaments] = useState(
    (seed?.required_filaments ?? []).join(", "),
  );
  const [requiredTags, setRequiredTags] = useState(
    (seed?.required_tags ?? []).join(", "),
  );
  const [tagMatchMode, setTagMatchMode] = useState<Job["tag_match_mode"]>(
    seed?.tag_match_mode ?? "all",
  );
  const [allowedPrinterIds, setAllowedPrinterIds] = useState<string[]>(
    seed?.allowed_printer_ids ?? [],
  );
  const [repetitions, setRepetitions] = useState(
    seed?.repetitions === null
      ? "infinite"
      : String(seed?.repetitions ?? 1),
  );
  const [loopMode, setLoopMode] = useState<Job["loop_mode"]>(
    seed?.loop_mode ?? "sticky",
  );
  const [autoRestart, setAutoRestart] = useState(seed?.auto_restart ?? false);
  const [autoClear, setAutoClear] = useState(seed?.auto_clear ?? false);
  const [overrideExisting, setOverrideExisting] = useState(
    seed?.override_existing_file ?? false,
  );
  const selectedFile = files.find((file) => file.id === fileId) ?? null;
  const selectedPrinter =
    printers.find((printer) => printer.id === targetPrinterId) ?? null;
  const routingPool = allowedPrinterIds.length
    ? printers.filter((printer) => allowedPrinterIds.includes(printer.id))
    : printers;
  const canAutoClear = Boolean(selectedFile?.auto_eject_verified);
  const eligibleAutoStartPrinters = routingPool.filter(
    (printer) =>
      printer.auto_start_enabled && supportsUnattendedStart(printer),
  );
  const canAutoRestart = Boolean(
    canAutoClear &&
      (selectedPrinter
        ? selectedPrinter.auto_start_enabled &&
          supportsUnattendedStart(selectedPrinter)
        : eligibleAutoStartPrinters.length) &&
      repetitions !== "1",
  );
  const updateFile = (nextFileId: string) => {
    const nextFile = files.find((file) => file.id === nextFileId);
    setFileId(nextFileId);
    if (nextFile) {
      setFilename(nextFile.filename);
      if (!material) setMaterial(nextFile.material);
      setRequiredManufacturer(nextFile.printer_manufacturer ?? "");
      setRequiredModel(nextFile.printer_model ?? "");
      setRequiredNozzle(
        nextFile.nozzle_diameter ? String(nextFile.nozzle_diameter) : "",
      );
      setRequiredFilaments(
        (nextFile.filament_profiles ?? []).join(", "),
      );
    }
    if (!nextFile?.auto_eject_verified) {
      setAutoClear(false);
      setAutoRestart(false);
    }
  };
  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const allowed =
      allowedPrinterIds.length && targetPrinterId
        ? Array.from(new Set([...allowedPrinterIds, targetPrinterId]))
        : allowedPrinterIds;
    onSubmit({
      name: String(form.get("name")),
      filename,
      target_printer_id: targetPrinterId || null,
      material,
      estimated_minutes: Number(form.get("estimated_minutes")) || 60,
      file_id: fileId || null,
      repetitions: repetitions === "infinite" ? null : Number(repetitions) || 1,
      loop_mode: loopMode,
      auto_restart: autoRestart && canAutoRestart,
      auto_clear: autoClear && canAutoClear,
      required_printer_manufacturer: requiredManufacturer.trim(),
      required_printer_model: requiredModel.trim(),
      required_nozzle_diameter: requiredNozzle
        ? Number(requiredNozzle)
        : null,
      required_filaments: commaList(requiredFilaments),
      required_tags: commaList(requiredTags),
      tag_match_mode: tagMatchMode,
      allowed_printer_ids: allowed,
      auto_switch_filament: false,
      override_existing_file: overrideExisting,
    });
  };
  return (
    <Modal
      eyebrow="PRODUCTION QUEUE"
      title={editing ? "Edit queued task" : "Queue a job"}
      copy={
        editing
          ? "Update the reusable parent task. Existing child run records remain immutable."
          : "A parent task stays local and queued while child records track each physical run."
      }
      onClose={onClose}
    >
      <form onSubmit={submit}>
        <label>
          Job name
          <input
            name="name"
            defaultValue={seed?.name ?? ""}
            placeholder="e.g. Fixture arm - left"
            required
          />
        </label>
        <label>
          Stored local file
          <select
            name="file_id"
            value={fileId}
            onChange={(event) => updateFile(event.target.value)}
          >
            <option value="">No uploaded local file attached</option>
            {files.map((file) => (
              <option value={file.id} key={file.id}>
                {file.name} ({file.filename})
              </option>
            ))}
          </select>
        </label>
        <label>
          File name
          <input
            name="filename"
            value={filename}
            onChange={(event) => setFilename(event.target.value)}
            placeholder="fixture-arm-left.gcode"
            required
          />
        </label>
        <div className="form-split">
          <label>
            Target printer
            <select
              name="target_printer_id"
              value={targetPrinterId}
              onChange={(event) => {
                setTargetPrinterId(event.target.value);
                setAutoRestart(false);
              }}
            >
              <option value="">Assign later</option>
              {printers.map((printer) => (
                <option value={printer.id} key={printer.id}>
                  {printer.name}
                </option>
              ))}
            </select>
          </label>
          <label>
            Estimated minutes
            <input
              name="estimated_minutes"
              type="number"
              min="1"
              defaultValue={seed?.estimated_minutes ?? 60}
            />
          </label>
        </div>
        <label>
          Material
          <input
            name="material"
            value={material}
            onChange={(event) => setMaterial(event.target.value)}
            placeholder="e.g. PETG - Orange"
          />
        </label>
        <section className="form-section">
          <div className="form-section-title">
            <strong>Compatibility rules</strong>
            <span>Leave a field blank to allow any value.</span>
          </div>
          <div className="form-split">
            <label>
              Required manufacturer
              <input
                name="required_printer_manufacturer"
                value={requiredManufacturer}
                onChange={(event) =>
                  setRequiredManufacturer(event.target.value)
                }
                placeholder="e.g. Bambu Lab"
              />
            </label>
            <label>
              Required printer model
              <input
                name="required_printer_model"
                value={requiredModel}
                onChange={(event) => setRequiredModel(event.target.value)}
                placeholder="e.g. P1S"
              />
            </label>
          </div>
          <label>
              Nozzle diameter (mm)
              <input
                name="required_nozzle_diameter"
                type="number"
                min="0.1"
                max="2"
                step="0.05"
                value={requiredNozzle}
                onChange={(event) => setRequiredNozzle(event.target.value)}
                placeholder="e.g. 0.4"
              />
          </label>
          <label>
            Required filament profiles
            <input
              name="required_filaments"
              value={requiredFilaments}
              onChange={(event) => setRequiredFilaments(event.target.value)}
              placeholder="e.g. PETG - Black, PLA Support"
            />
            <small className="field-help">
              Comma-separated profiles must match the printer's loaded slots in
              this exact order. Mirdain does not remap AMS or multi-material
              slots.
            </small>
          </label>
          <div className="tag-rule-grid">
            <label>
              Required printer tags
              <input
                name="required_tags"
                value={requiredTags}
                onChange={(event) => setRequiredTags(event.target.value)}
                placeholder="e.g. enclosed, production"
              />
            </label>
            <label>
              Tag match
              <select
                name="tag_match_mode"
                value={tagMatchMode}
                onChange={(event) =>
                  setTagMatchMode(
                    event.target.value as Job["tag_match_mode"],
                  )
                }
              >
                <option value="all">All required tags</option>
                <option value="any">Any required tag</option>
                <option value="exactly">Exactly this tag set</option>
              </select>
            </label>
          </div>
          <fieldset className="printer-restrictions">
            <legend>Allowed printers</legend>
            <p>
              No selection allows every otherwise-compatible printer. The
              assigned target is always included.
            </p>
            <div>
              {printers.map((printer) => (
                <label className="compact-check" key={printer.id}>
                  <input
                    type="checkbox"
                    checked={allowedPrinterIds.includes(printer.id)}
                    onChange={(event) =>
                      setAllowedPrinterIds((current) =>
                        event.target.checked
                          ? [...current, printer.id]
                          : current.filter((id) => id !== printer.id),
                      )
                    }
                  />
                  <span>
                    <strong>{printer.name}</strong>
                    <small>
                      {printer.model} · {printer.nozzle_diameter ?? 0.4} mm
                    </small>
                  </span>
                </label>
              ))}
            </div>
          </fieldset>
        </section>
        <section className="form-section">
          <div className="form-section-title">
            <strong>Production loop</strong>
            <span>Choose how repeated work returns to the queue.</span>
          </div>
        <div className="form-split">
          <label>
            Production runs
            <select
              name="repetitions"
              value={repetitions}
              onChange={(event) => {
                setRepetitions(event.target.value);
                if (event.target.value === "1") setAutoRestart(false);
              }}
            >
              <option value="1">One run</option>
              <option value="2">2 runs</option>
              <option value="5">5 runs</option>
              <option value="10">10 runs</option>
              <option value="infinite">
                Infinite repetitions (until stopped)
              </option>
            </select>
          </label>
          <label>
            Queue behavior
            <select
              name="loop_mode"
              value={loopMode}
              onChange={(event) =>
                setLoopMode(event.target.value as Job["loop_mode"])
              }
            >
              <option value="sticky">Sticky — keep next priority</option>
              <option value="cycle">Cycle — return to queue end</option>
            </select>
          </label>
        </div>
          <p className="loop-explainer">
            <strong>
              {repetitions === "infinite"
                ? "∞ Continuous production"
                : `${repetitions} planned run${repetitions === "1" ? "" : "s"}`}
            </strong>
            <span>
              {loopMode === "sticky"
                ? "Sticky repeats before unrelated jobs."
                : "Cycle gives other queued jobs a turn between repetitions."}
            </span>
          </p>
        </section>
        <label className="check-row clearance-check">
          <input
            name="auto_clear"
            type="checkbox"
            checked={autoClear}
            disabled={!canAutoClear}
            onChange={(event) => setAutoClear(event.target.checked)}
          />
          <span>
            <strong>Trust verified auto-clear evidence for this task</strong>
            <small>
              {canAutoClear
                ? "This is evidence and permission to mark the bed clear after an adapter-confirmed completion. It does not start another run. Manual COMPLETE always requires a later READY confirmation."
                : "Attach a file explicitly marked auto-eject verified before this clearance permission can be enabled."}
            </small>
          </span>
        </label>
        <label className="check-row">
          <input
            name="auto_restart"
            type="checkbox"
            checked={autoRestart}
            disabled={!canAutoRestart}
            onChange={(event) => setAutoRestart(event.target.checked)}
          />
          <span>
            <strong>Auto-restart after a verified ejection</strong>
            <small>
              {canAutoRestart
                ? "Separate dispatch permission: after either trusted auto-clear or an operator-confirmed READY, an armed compatible printer may claim another child run."
                : "Requires an auto-eject-verified file, more than one run, and at least one allowed printer with auto-start armed."}
            </small>
          </span>
        </label>
        <label className="check-row warning-check">
          <input
            name="override_existing_file"
            type="checkbox"
            checked={overrideExisting}
            onChange={(event) => setOverrideExisting(event.target.checked)}
          />
          <span>
            <strong>Force a fresh upload for every automated run</strong>
            <small>
              Off by default. Mirdain will not reuse its recorded delivery. The
              printer may reject another upload with the same filename; this
              setting does not authorize or guarantee an overwrite.
            </small>
          </span>
        </label>
        {selectedFile ? (
          <p className="quiet-copy">
            An uploaded local file is attached to this job and will be sent only when you choose Upload or an eligible verified automation runs.
          </p>
        ) : (
          <p className="quiet-copy">
            Create this job from the File Library to attach an uploaded print
            file.
          </p>
        )}
        {actions(onClose, editing ? "Save task" : "Add to queue")}
      </form>
    </Modal>
  );
}
function MaterialModal({
  printers,
  onClose,
  onSubmit,
}: {
  printers: Printer[];
  onClose: () => void;
  onSubmit: (payload: Record<string, unknown>) => void;
}) {
  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    onSubmit({
      name: String(form.get("name")),
      material_type: String(form.get("material_type")),
      brand: String(form.get("brand")),
      color: String(form.get("color")),
      remaining_percent: Number(form.get("remaining_percent")),
      spool_grams: Number(form.get("spool_grams")),
      assigned_printer_id: String(form.get("assigned_printer_id")) || null,
    });
  };
  return (
    <Modal
      eyebrow="MATERIAL SHELF"
      title="Add a spool"
      copy="Track what you have and assign the spool to a printer when it is loaded."
      onClose={onClose}
    >
      <form onSubmit={submit}>
        <label>
          Spool name
          <input name="name" placeholder="e.g. Black PETG" required />
        </label>
        <div className="form-split">
          <label>
            Material
            <select name="material_type" defaultValue="PLA">
              <option>PLA</option>
              <option>PETG</option>
              <option>ABS</option>
              <option>ASA</option>
              <option>TPU</option>
              <option>Other</option>
            </select>
          </label>
          <label>
            Brand
            <input name="brand" placeholder="e.g. Polymaker" />
          </label>
        </div>
        <div className="form-split">
          <label>
            Remaining %
            <input
              name="remaining_percent"
              type="number"
              min="0"
              max="100"
              defaultValue="100"
            />
          </label>
          <label>
            Spool grams
            <input
              name="spool_grams"
              type="number"
              min="1"
              defaultValue="1000"
            />
          </label>
        </div>
        <div className="form-split">
          <label>
            Color
            <input name="color" type="color" defaultValue="#8aaec9" />
          </label>
          <label>
            Assigned printer
            <select name="assigned_printer_id" defaultValue="">
              <option value="">On shelf</option>
              {printers.map((printer) => (
                <option value={printer.id} key={printer.id}>
                  {printer.name}
                </option>
              ))}
            </select>
          </label>
        </div>
        {actions(onClose, "Add spool")}
      </form>
    </Modal>
  );
}
function FileModal({
  onClose,
  onSubmit,
}: {
  onClose: () => void;
  onSubmit: (form: FormData) => void;
}) {
  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    form.set(
      "filament_profiles",
      JSON.stringify(commaList(String(form.get("filament_profiles")))),
    );
    onSubmit(form);
  };
  return (
    <Modal
      eyebrow="FILE LIBRARY"
      title="Upload a print file"
      copy="The original file is stored only in this local workspace. Supported formats are .gcode, .g, .gc, .3mf, and .bgcode (up to 512 MB)."
      onClose={onClose}
    >
      <form onSubmit={submit}>
        <label>
          Display name
          <input
            name="name"
            placeholder="e.g. Pallet clips - batch 04"
            required
          />
        </label>
        <label>
          Print file
          <input
            name="file"
            type="file"
            accept=".gcode,.g,.gc,.3mf,.bgcode"
            required
          />
        </label>
        <label>
          Estimated minutes
          <input
            name="estimated_minutes"
            type="number"
            min="1"
            placeholder="90"
          />
        </label>
        <label>
          Recommended material
          <input name="material" placeholder="e.g. PETG - Black" />
        </label>
        <label>
          Required printer manufacturer
          <input
            name="printer_manufacturer"
            placeholder="e.g. Bambu Lab (blank means any)"
          />
        </label>
        <div className="form-split">
          <label>
            Required printer model
            <input
              name="printer_model"
              placeholder="e.g. P1S (blank means any)"
            />
          </label>
          <label>
            Nozzle diameter (mm)
            <input
              name="nozzle_diameter"
              type="number"
              min="0.1"
              max="2"
              step="0.05"
              placeholder="0.4"
            />
          </label>
        </div>
        <label>
          Filament profiles
          <input
            name="filament_profiles"
            placeholder="e.g. PETG - Black, PLA Support"
          />
          <small className="field-help">
            Enter profiles in the sliced-file slot order. Enabled adapters do
            not remap AMS or multi-material slots.
          </small>
        </label>
        <label>
          Compatibility notes
          <input
            name="notes"
            placeholder="e.g. Verified with the P1S profile"
          />
        </label>
        <label className="check-row">
          <input name="auto_eject_verified" type="checkbox" />
          <span>
            <strong>This file has verified auto-ejection G-code</strong>
            <small>Required before a future auto-restart workflow can use this file.</small>
          </span>
        </label>
        {actions(onClose, "Store file locally")}
      </form>
    </Modal>
  );
}

export default App;
