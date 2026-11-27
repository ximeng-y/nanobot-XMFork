import type { WebUIIngressLimits } from "@/lib/types";

export const ingressLimitsFixture: WebUIIngressLimits = {
  transport: { max_frame_bytes: 36 * 1024 * 1024, envelope_reserve_bytes: 64 * 1024 },
  message: { max_text_bytes: 64 * 1024 },
  attachments: {
    max_count: 4,
    max_file_bytes: 6 * 1024 * 1024,
    max_total_bytes: 24 * 1024 * 1024,
    max_video_count: 1,
    max_video_bytes: 20 * 1024 * 1024,
  },
};
