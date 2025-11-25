<template>
    <div class="text-white flex gap-2 items-center justify-center uppercase text-xs">
        <div
            :class="{'bg-green-400': connectionStatus === 'Connected',
                     'bg-red-400': connectionStatus === 'Disconnected' || connectionStatus === 'Connection Failed',
                     'bg-yellow-400': connectionStatus === 'Connecting...'}" 
            class="p-1 rounded-full w-1 h-1 animate-pulse"></div>
    </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { io, Socket } from 'socket.io-client'

const connectionStatus = ref('Connecting...')
interface SocketStatusProps {
  connectionStatus: string
}
const socket: Socket = io('http://127.0.0.1:14562')

onMounted(() => {
  socket.on('connect', () => {
    connectionStatus.value = 'Connected'
  })
  socket.on('disconnect', () => {
    connectionStatus.value = 'Disconnected'
  })
  socket.on('connect_error', () => {
    connectionStatus.value = 'Connection Failed'
    alert('Unable to connect to the backend server. Please ensure it is running.');
  })
})
</script>