const { createApp } = Vue
createApp({
    data() {
        return {
            mensaje: 'Hola Mundo con Vue!',
            curso: 'Codo a Codo'
        }
    }
}).mount('#app')