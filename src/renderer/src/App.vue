<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { io, Socket } from 'socket.io-client'
import { shell } from 'electron'
import Versions from './components/Versions.vue'
import SocketStatus from './components/SocketStatus.vue'

interface Video {
  title: string
  path: string
}

interface Job {
  id: string
  kind: string
  status: string
  progress: number
  result?: any
  error?: string
}

const socket: Socket = io('http://127.0.0.1:14562')
const connectionStatus = ref('Connecting...')
const videos = ref<Video[]>([])
const loading = ref(true)
const jobs = ref<Record<string, Job>>({})
const showDownloadModal = ref(false)
const showClipModal = ref(false)
const showClipsModal = ref(false)
const showTemplatesModal = ref(false)
const downloadUrl = ref('')
const selectedVideo = ref('')
const selectedVideoForClips = ref('')
const clips = ref([])
const templates = ref([])
const selectedTemplate = ref('')
const customOverlays = ref([])
const fonts = ref([])
const customStart = ref(0)
const customDuration = ref(10)
const customOutputName = ref('')
const customResolution = ref('1080:1920')
const customFlip = ref(false)
const clipStart = ref('')
const clipEnd = ref('')
const clipText = ref('')
const clipOutputName = ref('')
const clipFlip = ref(false)

// const ipcHandle = (): void => window.electron.ipcRenderer.send('ping')

const openDownloadModal = () => {
  showDownloadModal.value = true
  downloadUrl.value = ''
}

const closeDownloadModal = () => {
  showDownloadModal.value = false
  downloadUrl.value = ''
}

const openClipModal = (videoPath: string) => {
  selectedVideo.value = videoPath
  showClipModal.value = true
  clipStart.value = ''
  clipEnd.value = ''
  clipText.value = ''
  clipOutputName.value = ''
}

const closeClipModal = () => {
  showClipModal.value = false
  selectedVideo.value = ''
  clipStart.value = ''
  clipEnd.value = ''
  clipText.value = ''
  clipOutputName.value = ''
  clipFlip.value = false
}

const startDownload = async () => {
  if (!downloadUrl.value.trim()) return

  try {
    const response = await fetch('http://127.0.0.1:14562/api/download', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url: downloadUrl.value.trim() })
    })
    const data = await response.json()
    console.log('Download started:', data.job_id)
    closeDownloadModal()
  } catch (error) {
    console.error('Download error:', error)
  }
}

const startClip = async () => {
  if (!selectedVideo.value || !clipStart.value || !clipEnd.value) return

  try {
    const response = await fetch('http://127.0.0.1:14562/api/clip', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        filename: selectedVideo.value.split('/').pop(),
        start: parseFloat(clipStart.value),
        end: parseFloat(clipEnd.value),
        text: clipText.value,
        output_name: clipOutputName.value || undefined,
        flip: clipFlip.value
      })
    })
    const data = await response.json()
    console.log('Clip started:', data.job_id)
    closeClipModal()
  } catch (error) {
    console.error('Clip error:', error)
  }
}

const playVideo = (videoPath: string) => {
  // Convert file path to API URL for main videos
  const filename = videoPath.split('/').pop()
  currentVideoSrc.value = `http://127.0.0.1:14562/video/${filename}`
  showVideoModal.value = true
}

const startTranscribe = async (videoPath: string) => {
  try {
    const response = await fetch('http://127.0.0.1:14562/api/transcribe', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        filename: videoPath.split('/').pop()
      })
    })
    const data = await response.json()
    console.log('Transcribe started:', data.job_id)
  } catch (error) {
    console.error('Transcribe error:', error)
  }
}

const showClipsForVideo = async (videoPath: string) => {
  selectedVideoForClips.value = videoPath
  const filename = videoPath.split('/').pop()
  try {
    const response = await fetch(`http://127.0.0.1:14562/api/clips/${filename}`)
    const data = await response.json()
    clips.value = data.clips
    showClipsModal.value = true
  } catch (error) {
    console.error('Error fetching clips:', error)
  }
}

const closeClipsModal = () => {
  showClipsModal.value = false
  selectedVideoForClips.value = ''
  clips.value = []
}

const showVideoModal = ref(false)
const currentVideoSrc = ref('')

const playClip = (clip: any) => {
  // Use the filename which should be in format "video_name/clip_file.mp4"
  currentVideoSrc.value = `http://127.0.0.1:14562/clips/${clip.filename}`
  showVideoModal.value = true
}

const closeVideoModal = () => {
  showVideoModal.value = false
  currentVideoSrc.value = ''
}

const openTemplatesModal = (videoPath: string) => {
  selectedVideo.value = videoPath
  fetchTemplates()
  fetchFonts()
  showTemplatesModal.value = true
}

const closeTemplatesModal = () => {
  showTemplatesModal.value = false
  selectedVideo.value = ''
  selectedTemplate.value = ''
  customOverlays.value = []
  customStart.value = 0
  customDuration.value = 10
  customOutputName.value = ''
  customResolution.value = '1080:1920'
  customFlip.value = false
}

const fetchTemplates = async () => {
  try {
    const response = await fetch('http://127.0.0.1:14562/api/templates')
    const data = await response.json()
    templates.value = data.templates
  } catch (error) {
    console.error('Error fetching templates:', error)
  }
}

const fetchFonts = async () => {
  try {
    const response = await fetch('http://127.0.0.1:14562/api/fonts')
    const data = await response.json()
    fonts.value = data.fonts
  } catch (error) {
    console.error('Error fetching fonts:', error)
  }
}

const selectTemplate = (templateName: string) => {
  selectedTemplate.value = templateName
  // Load template data for customization
  const template = templates.value.find(t => t.name === templateName)
  if (template) {
    customOverlays.value = JSON.parse(JSON.stringify(template.data.overlays || []))
    customStart.value = template.data.start || 0
    customDuration.value = template.data.duration || 10
    customOutputName.value = template.data.output_name || `${templateName}_{timestamp}.mp4`
    customResolution.value = template.data.resolution || '1080:1920'
    customFlip.value = template.data.flip || false
  }
}

const applyCustomTemplate = async () => {
  if (!selectedVideo.value || !selectedTemplate.value) return

  try {
    const response = await fetch('http://127.0.0.1:14562/api/clip-template', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        filename: selectedVideo.value.split('/').pop(),
        template_name: selectedTemplate.value,
        custom_overlays: customOverlays.value,
        custom_start: customStart.value,
        custom_duration: customDuration.value,
        custom_output_name: customOutputName.value,
        custom_resolution: customResolution.value,
        custom_flip: customFlip.value
      })
    })
    const data = await response.json()
    console.log('Custom template clip started:', data.job_id)
    closeTemplatesModal()
  } catch (error) {
    console.error('Custom template clip error:', error)
  }
}

const addOverlay = () => {
  customOverlays.value.push({
    type: 'text',
    text: 'New Text',
    x: '(w-text_w)/2',
    y: '(h-text_h)/2',
    fontSize: 28,
    textColor: 'white'
  })
}

const removeOverlay = (index: number) => {
  customOverlays.value.splice(index, 1)
}


onMounted(() => {
  socket.on('connect', () => {
    connectionStatus.value = 'Connected'
    socket.emit('get_videos')
  })

  socket.on('disconnect', () => {
    connectionStatus.value = 'Disconnected'
  })

  socket.on('connect_error', () => {
    connectionStatus.value = 'Connection Failed'
    loading.value = false
  })

  socket.on('videos_update', (data: Video[]) => {
    videos.value = data
    loading.value = false
  })

  socket.on('job_update', (job: Job) => {
    jobs.value[job.id] = job
    // Refresh videos list when download completes
    if (job.kind === 'download' && job.status === 'finished') {
      socket.emit('get_videos')
    }
  })

  socket.on('transcript_segment', (data: any) => {
    console.log('Transcript segment:', data)
  })
})
</script>

<template>
  <div class="min-h-screen bg-gray-50 font-sans">
    <header class="flex justify-between items-center p-2 px-5 bg-black shadow-sm">
      <h1 class="text-2xl font-thin text-white">VDO</h1>
      <div class="flex items-center gap-4">
        <button
          @click="openDownloadModal"
          class="bg-white hover:bg-white text-black px-4 py-2 rounded-full font-medium transition-colors"
        >
          Download Video
        </button>
        <SocketStatus  />
      </div>
    </header>

    <main class="p-8">
      <div v-if="loading" class="flex flex-col items-center justify-center h-64">
        <div class="w-10 h-10 border-4 border-gray-300 border-t-blue-600 rounded-full animate-spin"></div>
        <p class="mt-4 text-gray-600">Loading videos...</p>
      </div>

      <div v-else-if="videos.length === 0" class="text-center text-gray-600 text-xl">
        <p>No videos downloaded yet.</p>
      </div>

      <div v-else class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-6">
        <div
          v-for="video in videos"
          :key="video.path"
          class="bg-white rounded-xl shadow-md overflow-hidden transition-all hover:shadow-lg hover:-translate-y-1"
        >
          <div class="relative h-36 bg-gray-100 flex items-center justify-center group cursor-pointer" @click="playVideo(video.path)">
            <img src="./assets/electron.svg" alt="Video thumbnail" class="w-16 h-16 opacity-50" />
            <div class="absolute inset-0 flex items-center justify-center bg-black bg-opacity-0 group-hover:bg-opacity-70 transition-all">
              <div class="w-12 h-12 bg-black bg-opacity-70 rounded-full flex items-center justify-center">
                <span class="text-white text-xl">▶</span>
              </div>
            </div>
          </div>
          <div class="p-4">
            <h3 class="text-sm font-medium text-gray-900 line-clamp-2 mb-2">{{ video.title }}</h3>
            <div class="flex gap-2">
              <button
                @click.stop="playVideo(video.path)"
                class="text-xs bg-red-100 hover:bg-red-200 text-red-700 px-2 py-1 rounded"
              >
                Play
              </button>
              <button
                @click.stop="showClipsForVideo(video.path)"
                class="text-xs bg-orange-100 hover:bg-orange-200 text-orange-700 px-2 py-1 rounded"
              >
                Clips
              </button>
              <button
                @click.stop="startTranscribe(video.path)"
                class="text-xs bg-blue-100 hover:bg-blue-200 text-blue-700 px-2 py-1 rounded"
              >
                Transcribe
              </button>
              <button
                @click.stop="openClipModal(video.path)"
                class="text-xs bg-green-100 hover:bg-green-200 text-green-700 px-2 py-1 rounded"
              >
                Clip
              </button>
              <button
                @click.stop="openTemplatesModal(video.path)"
                class="text-xs bg-purple-100 hover:bg-purple-200 text-purple-700 px-2 py-1 rounded"
              >
                Templates
              </button>
            </div>
          </div>
        </div>
      </div>

      <!-- Jobs Status -->
      <div v-if="Object.keys(jobs).length > 0" class="mt-8">
        <h3 class="text-lg font-semibold mb-4">Active Jobs</h3>
        <div class="space-y-2">
          <div
            v-for="job in Object.values(jobs)"
            :key="job.id"
            class="bg-white p-4 rounded-lg shadow"
          >
            <div class="flex justify-between items-center mb-2">
              <span class="font-medium capitalize">{{ job.kind }}</span>
              <span :class="{
                'text-yellow-600': job.status === 'running',
                'text-green-600': job.status === 'finished',
                'text-red-600': job.status === 'error',
                'text-gray-600': job.status === 'queued'
              }">
                {{ job.status }}
              </span>
            </div>
            <div class="w-full bg-gray-200 rounded-full h-2">
              <div
                class="bg-blue-600 h-2 rounded-full transition-all"
                :style="{ width: job.progress + '%' }"
              ></div>
            </div>
            <p class="text-sm text-gray-600 mt-1">{{ job.progress.toFixed(1) }}%</p>
            <p v-if="job.error" class="text-sm text-red-600 mt-1">{{ job.error }}</p>
          </div>
        </div>
      </div>
    </main>

    <!-- Download Modal -->
    <div v-if="showDownloadModal" class="fixed inset-0 bg-black/80 backdrop-blur-sm flex items-center justify-center z-50" @click="closeDownloadModal">
      <div class="bg-white rounded-xl w-full max-w-md mx-4 max-h-[90vh] overflow-y-auto" @click.stop>
        <div class="flex justify-between items-center p-4">
          <h2 class="text-xl font-semibold text-gray-900">Download Video</h2>
          <button @click="closeDownloadModal" class="text-gray-400 hover:text-gray-600">&times;</button>
        </div>
        <div class="p-6">
          <form @submit.prevent="startDownload">
            <div class="mb-4">
              <label for="url" class="block text-sm font-medium text-gray-700 mb-2">Video URL:</label>
              <input
                id="url"
                v-model="downloadUrl"
                type="url"
                placeholder="https://..."
                required
                class="w-full px-3 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
              />
            </div>

            <div class="flex gap-3 justify-end">
              <button
                type="button"
                @click="closeDownloadModal"
                class="px-4 py-2 bg-gray-500 hover:bg-gray-600 text-white rounded-lg transition-colors"
              >
                Cancel
              </button>
              <button
                type="submit"
                :disabled="!downloadUrl.trim()"
                class="px-4 py-2 bg-blue-600 hover:bg-blue-700 disabled:bg-gray-300 text-white rounded-lg transition-colors"
              >
                Download
              </button>
            </div>
          </form>
        </div>
      </div>
    </div>

    <!-- Clip Modal -->
    <div v-if="showClipModal" class="fixed inset-0 bg-black/80 backdrop-blur-sm flex items-center justify-center z-50" @click="closeClipModal">
      <div class="bg-white rounded-xl w-full max-w-md mx-4 max-h-[90vh] overflow-y-auto" @click.stop>
        <div class="flex justify-between items-center p-4">
          <h2 class="text-xl font-semibold text-gray-900">Create Video Clip</h2>
          <button @click="closeClipModal" class="text-gray-400 hover:text-gray-600">&times;</button>
        </div>
        <div class="p-6">
          <form @submit.prevent="startClip">
            <div class="mb-4">
              <label for="start" class="block text-sm font-medium text-gray-700 mb-2">Start Time (seconds):</label>
              <input
                id="start"
                v-model="clipStart"
                type="number"
                step="0.1"
                min="0"
                required
                class="w-full px-3 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
              />
            </div>

            <div class="mb-4">
              <label for="end" class="block text-sm font-medium text-gray-700 mb-2">End Time (seconds):</label>
              <input
                id="end"
                v-model="clipEnd"
                type="number"
                step="0.1"
                min="0"
                required
                class="w-full px-3 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
              />
            </div>

            <div class="mb-4">
              <label for="text" class="block text-sm font-medium text-gray-700 mb-2">Overlay Text (optional):</label>
              <input
                id="text"
                v-model="clipText"
                type="text"
                placeholder="Text to overlay on clip"
                class="w-full px-3 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
              />
            </div>

            <div class="mb-4">
              <label for="output" class="block text-sm font-medium text-gray-700 mb-2">Output Filename (optional):</label>
              <input
                id="output"
                v-model="clipOutputName"
                type="text"
                placeholder="clip_output.mp4"
                class="w-full px-3 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
              />
            </div>

            <div class="mb-4">
              <label class="flex items-center text-sm font-medium text-gray-700">
                <input
                  v-model="clipFlip"
                  type="checkbox"
                  class="mr-2 w-4 h-4"
                />
                Flip Video Horizontally
              </label>
            </div>

            <div class="flex gap-3 justify-end">
              <button
                type="button"
                @click="closeClipModal"
                class="px-4 py-2 bg-gray-500 hover:bg-gray-600 text-white rounded-lg transition-colors"
              >
                Cancel
              </button>
              <button
                type="submit"
                :disabled="!clipStart || !clipEnd"
                class="px-4 py-2 bg-green-600 hover:bg-green-700 disabled:bg-gray-300 text-white rounded-lg transition-colors"
              >
                Create Clip
              </button>
            </div>
          </form>
        </div>
      </div>
    </div>

    <!-- Clips Display Modal -->
    <div v-if="showClipsModal" class="fixed inset-0 bg-black/80 backdrop-blur-sm flex items-center justify-center z-50" @click="closeClipsModal">
      <div class="bg-white rounded-xl w-full max-w-4xl mx-4 max-h-[90vh] overflow-y-auto" @click.stop>
        <div class="flex justify-between items-center p-6 border-b border-gray-200">
          <h2 class="text-xl font-semibold text-gray-900">Clips from Video</h2>
          <button @click="closeClipsModal" class="text-gray-400 hover:text-gray-600 text-2xl">&times;</button>
        </div>

        <div class="p-6">
          <div v-if="clips.length === 0" class="text-center py-8">
            <p class="text-gray-600">No clips have been created from this video yet.</p>
          </div>

          <div v-else class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
            <div
              v-for="clip in clips"
              :key="clip.filename"
              class="bg-gray-50 rounded-lg p-4 border border-gray-200"
            >
              <div class="relative h-32 bg-gray-200 flex items-center justify-center cursor-pointer group" @click="playClip(clip)">
                <div class="w-8 h-8 bg-black bg-opacity-70 rounded-full flex items-center justify-center">
                  <span class="text-white text-sm">▶</span>
                </div>
              </div>

              <div class="mt-3">
                <h3 class="text-sm font-medium text-gray-900 truncate">{{ clip.filename }}</h3>
                <p class="text-xs text-gray-600 mt-1">
                  {{ clip.start }}s - {{ clip.end }}s
                </p>
                <p v-if="clip.text" class="text-xs text-gray-500 mt-1 italic">
                  "{{ clip.text }}"
                </p>
                <p class="text-xs text-gray-400 mt-2">
                  Created: {{ new Date(clip.created_at * 1000).toLocaleString() }}
                </p>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- Templates Modal -->
    <div v-if="showTemplatesModal" class="fixed inset-0 bg-black/80 backdrop-blur-sm flex items-center justify-center z-50" @click="closeTemplatesModal">
      <div class="bg-white rounded-xl w-full max-w-6xl mx-4 max-h-[90vh] overflow-y-auto" @click.stop>
        <div class="flex justify-between items-center p-6 border-b border-gray-200">
          <h2 class="text-xl font-semibold text-gray-900">
            {{ selectedTemplate ? `Customize ${selectedTemplate}` : 'Select Template' }}
          </h2>
          <button @click="closeTemplatesModal" class="text-gray-400 hover:text-gray-600 text-2xl">&times;</button>
        </div>

        <div class="p-6">
          <!-- Template Selection -->
          <div v-if="!selectedTemplate">
            <div v-if="templates.length === 0" class="text-center py-8">
              <p class="text-gray-600">No templates available. Create some template JSON files in the templates directory.</p>
            </div>

            <div v-else class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
              <div
                v-for="template in templates"
                :key="template.name"
                class="bg-gray-50 rounded-lg p-4 border border-gray-200 hover:border-purple-300 cursor-pointer transition-colors"
                @click="selectTemplate(template.name)"
              >
                <div class="text-center">
                  <div class="w-12 h-12 bg-purple-100 rounded-full flex items-center justify-center mx-auto mb-3">
                    <span class="text-purple-600 text-xl">📹</span>
                  </div>
                  <h3 class="text-sm font-medium text-gray-900">{{ template.name }}</h3>
                  <p class="text-xs text-gray-600 mt-1">{{ template.data.description || 'Template for video clipping' }}</p>
                  <div class="mt-2 text-xs text-gray-500">
                    <p>Duration: {{ template.data.duration || 'N/A' }}s</p>
                    <p>Resolution: {{ template.data.resolution || 'N/A' }}</p>
                  </div>
                </div>
              </div>
            </div>
          </div>

          <!-- Template Customization -->
          <div v-else>
            <div class="mb-6">
              <button @click="selectedTemplate = ''" class="text-blue-600 hover:text-blue-800 text-sm">&larr; Back to templates</button>
            </div>

            <!-- Template Properties -->
            <div class="bg-blue-50 rounded-lg p-4 border border-blue-200 mb-6">
              <h4 class="text-sm font-medium text-blue-900 mb-4">Template Settings</h4>
              <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
                <div>
                  <label class="block text-xs font-medium text-blue-700 mb-1">Start Time (seconds)</label>
                  <input
                    v-model.number="customStart"
                    type="number"
                    step="0.1"
                    min="0"
                    class="w-full px-2 py-1 text-sm border border-blue-300 rounded focus:outline-none focus:ring-1 focus:ring-blue-500"
                  />
                </div>
                <div>
                  <label class="block text-xs font-medium text-blue-700 mb-1">Duration (seconds)</label>
                  <input
                    v-model.number="customDuration"
                    type="number"
                    step="0.1"
                    min="1"
                    class="w-full px-2 py-1 text-sm border border-blue-300 rounded focus:outline-none focus:ring-1 focus:ring-blue-500"
                  />
                </div>
                <div>
                  <label class="block text-xs font-medium text-blue-700 mb-1">Output Filename</label>
                  <input
                    v-model="customOutputName"
                    type="text"
                    placeholder="output_{timestamp}.mp4"
                    class="w-full px-2 py-1 text-sm border border-blue-300 rounded focus:outline-none focus:ring-1 focus:ring-blue-500"
                  />
                </div>
                <div>
                  <label class="block text-xs font-medium text-blue-700 mb-1">Resolution</label>
                  <select
                    v-model="customResolution"
                    class="w-full px-2 py-1 text-sm border border-blue-300 rounded focus:outline-none focus:ring-1 focus:ring-blue-500"
                  >
                    <option value="1080:1920">1080x1920 (Mobile)</option>
                    <option value="1920:1080">1920x1080 (Landscape)</option>
                    <option value="720:1280">720x1280 (Mobile HD)</option>
                    <option value="1280:720">1280x720 (HD Landscape)</option>
                    <option value="original">Original (No scaling)</option>
                  </select>
                </div>
                <div>
                  <label class="flex items-center text-xs font-medium text-blue-700">
                    <input
                      v-model="customFlip"
                      type="checkbox"
                      class="mr-2"
                    />
                    Flip Video Horizontally
                  </label>
                </div>
              </div>
            </div>

            <div class="space-y-6">
              <div v-for="(overlay, index) in customOverlays" :key="index" class="bg-gray-50 rounded-lg p-4 border border-gray-200">
                <div class="flex justify-between items-center mb-4">
                  <h4 class="text-sm font-medium text-gray-900">Overlay {{ index + 1 }} ({{ overlay.type }})</h4>
                  <button @click="removeOverlay(index)" class="text-red-600 hover:text-red-800 text-sm">Remove</button>
                </div>

                <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
                  <!-- Text Content -->
                  <div v-if="overlay.type === 'text'">
                    <label class="block text-xs font-medium text-gray-700 mb-1">Text</label>
                    <input
                      v-model="overlay.text"
                      type="text"
                      class="w-full px-2 py-1 text-sm border border-gray-300 rounded focus:outline-none focus:ring-1 focus:ring-blue-500"
                    />
                  </div>

                  <!-- Emoji Content -->
                  <div v-if="overlay.type === 'emoji'">
                    <label class="block text-xs font-medium text-gray-700 mb-1">Emoji</label>
                    <input
                      v-model="overlay.emoji"
                      type="text"
                      class="w-full px-2 py-1 text-sm border border-gray-300 rounded focus:outline-none focus:ring-1 focus:ring-blue-500"
                    />
                  </div>

                  <!-- Position X -->
                  <div>
                    <label class="block text-xs font-medium text-gray-700 mb-1">X Position</label>
                    <input
                      v-model="overlay.x"
                      type="text"
                      placeholder="(w-text_w)/2"
                      class="w-full px-2 py-1 text-sm border border-gray-300 rounded focus:outline-none focus:ring-1 focus:ring-blue-500"
                    />
                  </div>

                  <!-- Position Y -->
                  <div>
                    <label class="block text-xs font-medium text-gray-700 mb-1">Y Position</label>
                    <input
                      v-model="overlay.y"
                      type="text"
                      placeholder="(h-text_h)/2"
                      class="w-full px-2 py-1 text-sm border border-gray-300 rounded focus:outline-none focus:ring-1 focus:ring-blue-500"
                    />
                  </div>

                  <!-- Font Size -->
                  <div>
                    <label class="block text-xs font-medium text-gray-700 mb-1">Font Size</label>
                    <input
                      v-model.number="overlay.fontSize"
                      type="number"
                      min="8"
                      max="200"
                      class="w-full px-2 py-1 text-sm border border-gray-300 rounded focus:outline-none focus:ring-1 focus:ring-blue-500"
                    />
                  </div>

                  <!-- Font Family -->
                  <div>
                    <label class="block text-xs font-medium text-gray-700 mb-1">Font Family</label>
                    <select
                      v-model="overlay.font"
                      class="w-full px-2 py-1 text-sm border border-gray-300 rounded focus:outline-none focus:ring-1 focus:ring-blue-500"
                    >
                      <option value="">Default (Noto Emoji)</option>
                      <option
                        v-for="font in fonts"
                        :key="font.name"
                        :value="font.path"
                      >
                        {{ font.name }}
                      </option>
                    </select>
                  </div>

                  <!-- Text Color -->
                  <div>
                    <label class="block text-xs font-medium text-gray-700 mb-1">Text Color</label>
                    <input
                      v-model="overlay.textColor"
                      type="text"
                      placeholder="white"
                      class="w-full px-2 py-1 text-sm border border-gray-300 rounded focus:outline-none focus:ring-1 focus:ring-blue-500"
                    />
                  </div>

                  <!-- Box Background -->
                  <div>
                    <label class="flex items-center text-xs font-medium text-gray-700">
                      <input
                        v-model="overlay.box"
                        type="checkbox"
                        class="mr-2"
                      />
                      Background Box
                    </label>
                    <input
                      v-if="overlay.box"
                      v-model="overlay.boxColor"
                      type="text"
                      placeholder="black@0.6"
                      class="w-full mt-1 px-2 py-1 text-sm border border-gray-300 rounded focus:outline-none focus:ring-1 focus:ring-blue-500"
                    />
                  </div>

                  <!-- Shadow -->
                  <div>
                    <label class="flex items-center text-xs font-medium text-gray-700">
                      <input
                        v-model="overlay.shadow"
                        type="checkbox"
                        class="mr-2"
                      />
                      Shadow
                    </label>
                  </div>

                  <!-- Stroke -->
                  <div>
                    <label class="flex items-center text-xs font-medium text-gray-700">
                      <input
                        v-model="overlay.stroke"
                        type="checkbox"
                        class="mr-2"
                      />
                      Stroke
                    </label>
                    <input
                      v-if="overlay.stroke"
                      v-model="overlay.strokeColor"
                      type="text"
                      placeholder="black"
                      class="w-full mt-1 px-2 py-1 text-sm border border-gray-300 rounded focus:outline-none focus:ring-1 focus:ring-blue-500"
                    />
                  </div>
                </div>
              </div>

              <div class="flex gap-4">
                <button
                  @click="addOverlay"
                  class="px-4 py-2 bg-green-600 hover:bg-green-700 text-white rounded-lg text-sm"
                >
                  Add Overlay
                </button>
              </div>
            </div>

            <div class="flex gap-3 justify-end mt-6 pt-6 border-t border-gray-200">
              <button
                @click="closeTemplatesModal"
                class="px-4 py-2 bg-gray-500 hover:bg-gray-600 text-white rounded-lg transition-colors"
              >
                Cancel
              </button>
              <button
                @click="applyCustomTemplate"
                class="px-4 py-2 bg-purple-600 hover:bg-purple-700 text-white rounded-lg transition-colors"
              >
                Apply Template
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- Video Player Modal -->
    <div v-if="showVideoModal" class="fixed inset-0 bg-black flex items-center justify-center z-50" @click="closeVideoModal">
      <div class="relative w-full max-w-4xl mx-4" @click.stop>
        <button
          @click="closeVideoModal"
          class="absolute top-4 right-4 text-white bg-black bg-opacity-50 rounded-full w-10 h-10 flex items-center justify-center hover:bg-opacity-70 z-10"
        >
          &times;
        </button>
        <video
          v-if="currentVideoSrc"
          :src="currentVideoSrc"
          controls
          autoplay
          class="w-full rounded-lg"
        >
          Your browser does not support the video tag.
        </video>
      </div>
    </div>

    <Versions />
  </div>
</template>

