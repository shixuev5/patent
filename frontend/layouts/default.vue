<template>
  <div class="min-h-screen flex flex-col bg-white">
    <slot />
  </div>
</template>

<style>
html {
  scroll-behavior: smooth;
}

body {
  overflow-x: hidden;
}
</style>
