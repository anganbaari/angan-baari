// ================================================================
// ANGAN BAARI — MAIN JAVASCRIPT
// ================================================================

(function () {

    'use strict';

    /* ============================================================
       GLOBAL SETTINGS
    ============================================================ */

    const site = document.documentElement;

    const body = document.body;


    /* ============================================================
       MOBILE / DESKTOP NAVIGATION
    ============================================================ */

    const navbar =
        document.getElementById('navbar');

    const hamburger =
        document.getElementById('hamburger');

    const mobileDrawer =
        document.getElementById('mobileDrawer');

    const drawerOverlay =
        document.getElementById('drawerOverlay');

    const drawerClose =
        document.getElementById('drawerClose');


    function openDrawer() {

        if (!mobileDrawer) return;

        mobileDrawer.classList.add('open');

        if (drawerOverlay) {
            drawerOverlay.classList.add('open');
        }

        if (hamburger) {
            hamburger.classList.add('active');
        }

        body.classList.add('drawer-open');
    }


    function closeDrawer() {

        if (!mobileDrawer) return;

        mobileDrawer.classList.remove('open');

        if (drawerOverlay) {
            drawerOverlay.classList.remove('open');
        }

        if (hamburger) {
            hamburger.classList.remove('active');
        }

        body.classList.remove('drawer-open');
    }


    if (hamburger) {

        hamburger.addEventListener(
            'click',
            function () {

                if (
                    mobileDrawer &&
                    mobileDrawer.classList.contains('open')
                ) {
                    closeDrawer();
                } else {
                    openDrawer();
                }

            }
        );

    }


    if (drawerClose) {

        drawerClose.addEventListener(
            'click',
            closeDrawer
        );

    }


    if (drawerOverlay) {

        drawerOverlay.addEventListener(
            'click',
            closeDrawer
        );

    }


    /* ============================================================
       CLOSE DRAWER WHEN LINK IS CLICKED
    ============================================================ */

    if (mobileDrawer) {

        const drawerLinks =
            mobileDrawer.querySelectorAll(
                'a'
            );


        drawerLinks.forEach(
            function (link) {

                link.addEventListener(
                    'click',
                    closeDrawer
                );

            }
        );

    }


    /* ============================================================
       NAVBAR SCROLL EFFECT
    ============================================================ */

    let lastScroll =
        window.scrollY;


    function updateNavbar() {

        if (!navbar) return;

        const currentScroll =
            window.scrollY;


        if (currentScroll > 40) {

            navbar.classList.add(
                'scrolled'
            );

        } else {

            navbar.classList.remove(
                'scrolled'
            );

        }


        lastScroll =
            currentScroll;
    }


    window.addEventListener(
        'scroll',
        updateNavbar,
        {
            passive: true
        }
    );


    updateNavbar();


    /* ============================================================
       NAVIGATION ACTIVE SECTION
    ============================================================ */

    const sections =
        document.querySelectorAll(
            'section[id]'
        );


    const navLinks =
        document.querySelectorAll(
            '.desktop-nav a[href^="#"], ' +
            '.mobile-drawer a[href^="#"]'
        );


    function updateActiveNavigation() {

        const scrollPosition =
            window.scrollY +
            window.innerHeight *
            0.35;


        let currentSection =
            '';


        sections.forEach(
            function (section) {

                const top =
                    section.offsetTop;

                const bottom =
                    top +
                    section.offsetHeight;


                if (
                    scrollPosition >= top &&
                    scrollPosition < bottom
                ) {

                    currentSection =
                        section.id;
                }

            }
        );


        navLinks.forEach(
            function (link) {

                const href =
                    link.getAttribute('href');


                if (
                    href ===
                    '#' + currentSection
                ) {

                    link.classList.add(
                        'active'
                    );

                } else {

                    link.classList.remove(
                        'active'
                    );

                }

            }
        );

    }


    window.addEventListener(
        'scroll',
        updateActiveNavigation,
        {
            passive: true
        }
    );


    updateActiveNavigation();


    /* ============================================================
       SMOOTH SCROLL
    ============================================================ */

    document
        .querySelectorAll(
            'a[href^="#"]'
        )
        .forEach(
            function (link) {

                link.addEventListener(
                    'click',
                    function (event) {

                        const targetId =
                            this.getAttribute(
                                'href'
                            );


                        if (
                            !targetId ||
                            targetId === '#'
                        ) {
                            return;
                        }


                        const target =
                            document.querySelector(
                                targetId
                            );


                        if (!target) {
                            return;
                        }


                        event.preventDefault();


                        const navbarHeight =
                            navbar ?
                            navbar.offsetHeight :
                            0;


                        const targetPosition =
                            target.getBoundingClientRect()
                                .top +
                            window.scrollY -
                            navbarHeight;


                        window.scrollTo({
                            top:
                                targetPosition,
                            behavior:
                                'smooth'
                        });

                    }
                );

            }
        );


    /* ============================================================
       HERO CAROUSEL
    ============================================================ */

    const heroSlides =
        document.querySelectorAll(
            '.hero-slide'
        );


    const heroDots =
        document.querySelectorAll(
            '.hero-dot'
        );


    let heroIndex =
        0;


    let heroTimer =
        null;


    function showHeroSlide(index) {

        if (!heroSlides.length) {
            return;
        }


        heroIndex =
            (
                index +
                heroSlides.length
            ) %
            heroSlides.length;


        heroSlides.forEach(
            function (slide, i) {

                slide.classList.toggle(
                    'active',
                    i === heroIndex
                );

            }
        );


        heroDots.forEach(
            function (dot, i) {

                dot.classList.toggle(
                    'active',
                    i === heroIndex
                );

            }
        );

    }


    function nextHeroSlide() {

        showHeroSlide(
            heroIndex + 1
        );

    }


    function startHeroTimer() {

        if (
            heroTimer ||
            heroSlides.length < 2
        ) {
            return;
        }


        heroTimer =
            setInterval(
                nextHeroSlide,
                6000
            );

    }


    function resetHeroTimer() {

        if (heroTimer) {

            clearInterval(
                heroTimer
            );

            heroTimer =
                null;
        }


        startHeroTimer();

    }


    heroDots.forEach(
        function (dot, index) {

            dot.addEventListener(
                'click',
                function () {

                    showHeroSlide(
                        index
                    );

                    resetHeroTimer();

                }
            );

        }
    );


    showHeroSlide(0);

    startHeroTimer();


    /* ============================================================
       HERO SWIPE
    ============================================================ */

    const hero =
        document.querySelector(
            '.hero'
        );


    let touchStartX =
        0;

    let touchEndX =
        0;


    if (hero) {

        hero.addEventListener(
            'touchstart',
            function (event) {

                touchStartX =
                    event.changedTouches[0]
                        .screenX;

            },
            {
                passive: true
            }
        );


        hero.addEventListener(
            'touchend',
            function (event) {

                touchEndX =
                    event.changedTouches[0]
                        .screenX;


                const distance =
                    touchEndX -
                    touchStartX;


                if (
                    Math.abs(distance) <
                    40
                ) {
                    return;
                }


                if (distance < 0) {

                    showHeroSlide(
                        heroIndex + 1
                    );

                } else {

                    showHeroSlide(
                        heroIndex - 1
                    );

                }


                resetHeroTimer();

            },
            {
                passive: true
            }
        );

    }


    /* ============================================================
       INTERSECTION OBSERVER
    ============================================================ */

    const revealElements =
        document.querySelectorAll(
            '.reveal, ' +
            '.fade-up, ' +
            '.fade-in, ' +
            '.slide-up'
        );


    if (
        'IntersectionObserver' in window
    ) {

        const revealObserver =
            new IntersectionObserver(
                function (entries, observer) {

                    entries.forEach(
                        function (entry) {

                            if (
                                !entry.isIntersecting
                            ) {
                                return;
                            }


                            entry.target.classList.add(
                                'visible'
                            );


                            observer.unobserve(
                                entry.target
                            );

                        }
                    );

                },
                {
                    threshold: 0.12,
                    rootMargin:
                        '0px 0px -40px 0px'
                }
            );


        revealElements.forEach(
            function (element) {

                revealObserver.observe(
                    element
                );

            }
        );

    } else {

        revealElements.forEach(
            function (element) {

                element.classList.add(
                    'visible'
                );

            }
        );

    }


    /* ============================================================
       COUNTERS
    ============================================================ */

    const counters =
        document.querySelectorAll(
            '[data-count]'
        );


    function animateCounter(
        element
    ) {

        const target =
            parseFloat(
                element.dataset.count
            );


        if (
            Number.isNaN(target)
        ) {
            return;
        }


        const duration =
            1800;


        const start =
            performance.now();


        function update(
            currentTime
        ) {

            const progress =
                Math.min(
                    (
                        currentTime -
                        start
                    ) /
                    duration,
                    1
                );


            const eased =
                1 -
                Math.pow(
                    1 - progress,
                    3
                );


            const value =
                target *
                eased;


            if (
                Number.isInteger(
                    target
                )
            ) {

                element.textContent =
                    Math.round(
                        value
                    );

            } else {

                element.textContent =
                    value.toFixed(1);

            }


            if (
                progress < 1
            ) {

                requestAnimationFrame(
                    update
                );

            }

        }


        requestAnimationFrame(
            update
        );

    }


    if (
        'IntersectionObserver' in window
    ) {

        const counterObserver =
            new IntersectionObserver(
                function (
                    entries,
                    observer
                ) {

                    entries.forEach(
                        function (entry) {

                            if (
                                !entry.isIntersecting
                            ) {
                                return;
                            }


                            animateCounter(
                                entry.target
                            );


                            observer.unobserve(
                                entry.target
                            );

                        }
                    );

                },
                {
                    threshold: 0.5
                }
            );


        counters.forEach(
            function (counter) {

                counterObserver.observe(
                    counter
                );

            }
        );

    }


    /* ============================================================
       PRODUCTS FILTER
    ============================================================ */

    const filterButtons =
        document.querySelectorAll(
            '[data-filter]'
        );


    const productCards =
        document.querySelectorAll(
            '[data-category]'
        );


    filterButtons.forEach(
        function (button) {

            button.addEventListener(
                'click',
                function () {

                    const filter =
                        this.dataset.filter;


                    filterButtons.forEach(
                        function (btn) {

                            btn.classList.remove(
                                'active'
                            );

                        }
                    );


                    this.classList.add(
                        'active'
                    );


                    productCards.forEach(
                        function (card) {

                            const category =
                                card.dataset.category;


                            const visible =
                                filter ===
                                'all' ||
                                category ===
                                filter;


                            card.style.display =
                                visible ?
                                '' :
                                'none';

                        }
                    );

                }
            );

        }
    );


    /* ============================================================
       GALLERY
    ============================================================ */

    const galleryItems =
        document.querySelectorAll(
            '.gallery-item'
        );


    const lightbox =
        document.getElementById(
            'lightbox'
        );


    const lightboxImage =
        document.getElementById(
            'lightboxImage'
        );


    const lightboxClose =
        document.getElementById(
            'lightboxClose'
        );


    function openLightbox(
        item
    ) {

        if (
            !lightbox ||
            !lightboxImage
        ) {
            return;
        }


        const image =
            item.querySelector(
                'img'
            );


        if (!image) {
            return;
        }


        lightboxImage.src =
            image.currentSrc ||
            image.src;


        lightboxImage.alt =
            image.alt ||
            'Angan Baari';


        lightbox.classList.add(
            'open'
        );


        body.classList.add(
            'lightbox-open'
        );

    }


    function closeLightbox() {

        if (!lightbox) {
            return;
        }


        lightbox.classList.remove(
            'open'
        );


        body.classList.remove(
            'lightbox-open'
        );

    }


    galleryItems.forEach(
        function (item) {

            item.addEventListener(
                'click',
                function () {

                    openLightbox(
                        this
                    );

                }
            );

        }
    );


    if (lightboxClose) {

        lightboxClose.addEventListener(
            'click',
            closeLightbox
        );

    }


    if (lightbox) {

        lightbox.addEventListener(
            'click',
            function (event) {

                if (
                    event.target ===
                    lightbox
                ) {

                    closeLightbox();

                }

            }
        );

    }


    document.addEventListener(
        'keydown',
        function (event) {

            if (
                event.key ===
                'Escape'
            ) {

                closeLightbox();

            }

        }
    );


    /* ============================================================
       DELIVERY CAROUSEL
    ============================================================ */

    const deliveryTrack =
        document.querySelector(
            '.delivery-track'
        );


    const deliveryCards =
        document.querySelectorAll(
            '.delivery-card'
        );


    const deliveryPrev =
        document.querySelector(
            '.delivery-prev'
        );


    const deliveryNext =
        document.querySelector(
            '.delivery-next'
        );


    let deliveryIndex =
        0;


    function updateDelivery() {

        if (
            !deliveryTrack ||
            !deliveryCards.length
        ) {
            return;
        }


        const card =
            deliveryCards[0];


        const gap =
            parseFloat(
                getComputedStyle(
                    deliveryTrack
                ).gap
            ) || 0;


        const offset =
            deliveryIndex *
            (
                card.offsetWidth +
                gap
            );


        deliveryTrack.style.transform =
            `translateX(-${offset}px)`;

    }


    if (deliveryNext) {

        deliveryNext.addEventListener(
            'click',
            function () {

                if (
                    deliveryIndex <
                    deliveryCards.length - 1
                ) {

                    deliveryIndex++;

                    updateDelivery();

                }

            }
        );

    }


    if (deliveryPrev) {

        deliveryPrev.addEventListener(
            'click',
            function () {

                if (
                    deliveryIndex >
                    0
                ) {

                    deliveryIndex--;

                    updateDelivery();

                }

            }
        );

    }


    window.addEventListener(
        'resize',
        updateDelivery
    );


    /* ============================================================
       NEWSLETTER FORM
    ============================================================ */

    const newsletterForm =
        document.querySelector(
            '.newsletter-form'
        );


    if (newsletterForm) {

        newsletterForm.addEventListener(
            'submit',
            function (event) {

                event.preventDefault();


                const email =
                    newsletterForm.querySelector(
                        'input[type="email"]'
                    );


                if (
                    !email ||
                    !email.value.trim()
                ) {
                    return;
                }


                const button =
                    newsletterForm.querySelector(
                        'button'
                    );


                const originalText =
                    button ?
                    button.textContent :
                    'Subscribe';


                if (button) {

                    button.textContent =
                        'Subscribed ✓';

                    button.disabled =
                        true;

                }


                setTimeout(
                    function () {

                        newsletterForm.reset();


                        if (button) {

                            button.textContent =
                                originalText;

                            button.disabled =
                                false;

                        }

                    },
                    2500
                );

            }
        );

    }


    /* ============================================================
       CONTACT FORM
    ============================================================ */

    const contactForm =
        document.querySelector(
            '#contactForm'
        );


    if (contactForm) {

        contactForm.addEventListener(
            'submit',
            function (event) {

                const requiredFields =
                    contactForm.querySelectorAll(
                        '[required]'
                    );


                let valid =
                    true;


                requiredFields.forEach(
                    function (field) {

                        if (
                            !field.value.trim()
                        ) {

                            valid =
                                false;

                            field.classList.add(
                                'error'
                            );

                        } else {

                            field.classList.remove(
                                'error'
                            );

                        }

                    }
                );


                if (!valid) {

                    event.preventDefault();

                }

            }
        );

    }


    /* ============================================================
       SCROLL TO TOP
    ============================================================ */

    const scrollTopButton =
        document.querySelector(
            '.scroll-top'
        );


    if (scrollTopButton) {

        window.addEventListener(
            'scroll',
            function () {

                scrollTopButton.classList.toggle(
                    'show',
                    window.scrollY >
                    500
                );

            },
            {
                passive: true
            }
        );


        scrollTopButton.addEventListener(
            'click',
            function () {

                window.scrollTo({
                    top: 0,
                    behavior: 'smooth'
                });

            }
        );

    }


    /* ============================================================
       AOS
    ============================================================ */

    if (
        typeof AOS !== 'undefined'
    ) {

        AOS.init({
            duration: 800,
            easing: 'ease-out-cubic',
            once: true,
            offset: 60
        });

    }


    /* ============================================================
       FLAG — NATURAL WIND-BLOWN NEPAL FLAG
       
       SPEED  = 4.0
       MAIN   = 34
       SECOND = 6
       EDGE   = 15
    ============================================================ */

    (function () {

        const canvas =
            document.getElementById(
                'navNepalFlag'
            );


        if (!canvas) {
            return;
        }


        const WIDTH =
            450;


        const HEIGHT =
            600;


        const FLAG_W =
            330;


        const FLAG_H =
            441;


        const PAD_X =
            60;


        const PAD_Y =
            78;


        canvas.width =
            WIDTH;


        canvas.height =
            HEIGHT;


        const ctx =
            canvas.getContext(
                '2d',
                {
                    alpha: true
                }
            );


        if (!ctx) {
            return;
        }


        ctx.imageSmoothingEnabled =
            false;


        /* ========================================================
           ORIGINAL NEPAL FLAG SVG
        ======================================================== */

        const svg = `
        <svg
            xmlns="http://www.w3.org/2000/svg"
            width="110"
            height="147"
            viewBox="0 0 110 147">

            <path
                d="
                M 0.00,0.00
                L 110.00,68.88
                L 32.22,68.88
                L 110.00,146.67
                L 0.00,146.67
                Z"
                fill="#B80F2F"
                stroke="#001F5B"
                stroke-width="3.5"
                stroke-linejoin="round"
            />

            <g transform="translate(27.50,43.05)">

                <path
                    d="
                    M 22.27,-4.58
                    L 21.52,-4.79
                    L 20.33,-2.42
                    L 15.70,3.07
                    L 12.48,5.11
                    L 11.18,4.57
                    L 10.86,2.96
                    L 12.80,1.99
                    L 13.23,0.48
                    L 10.00,-0.05
                    L 10.21,-1.45
                    L 11.93,-4.36
                    L 7.74,-3.72
                    L 7.95,-7.81
                    L 6.34,-7.48
                    L 4.18,-5.87
                    L 2.89,-10.06
                    L 0.52,-7.05
                    L -0.23,-7.16
                    L -2.17,-9.74
                    L -3.78,-5.98
                    L -6.69,-7.81
                    L -7.98,-7.92
                    L -7.33,-7.59
                    L -7.33,-3.72
                    L -9.38,-3.61
                    L -11.10,-4.25
                    L -9.81,-0.05
                    L -13.04,0.59
                    L -12.93,1.56
                    L -10.89,3.18
                    L -11.64,5.33
                    L -13.04,5.00
                    L -15.62,3.07
                    L -18.43,0.16
                    L -20.90,-3.61
                    L -21.55,-3.61
                    L -20.68,1.77
                    L -17.99,6.51
                    L -14.87,9.95
                    L -10.24,13.30
                    L -3.46,15.34
                    L 1.93,15.56
                    L 9.89,13.73
                    L 14.74,10.39
                    L 20.23,3.50
                    L 22.16,-2.42
                    Z"
                    fill="#FFFFFF"
                />

            </g>

            <path
                d="
                M 27.50,131.32
                L 23.64,122.20
                L 15.73,128.16
                L 16.94,118.33
                L 7.11,119.55
                L 13.08,111.64
                L 3.96,107.78
                L 13.08,103.91
                L 7.11,96.01
                L 16.94,97.22
                L 15.73,87.39
                L 23.64,93.35
                L 27.50,84.24
                L 31.36,93.35
                L 39.27,87.39
                L 38.06,97.22
                L 47.89,96.01
                L 41.92,103.91
                L 51.04,107.78
                L 41.92,111.64
                L 47.89,119.55
                L 38.06,118.33
                L 39.27,128.16
                L 31.36,122.20
                Z"
                fill="#FFFFFF"
            />

        </svg>
        `;


        /* ========================================================
           SOURCE CANVAS
        ======================================================== */

        const sourceCanvas =
            document.createElement(
                'canvas'
            );


        sourceCanvas.width =
            WIDTH;


        sourceCanvas.height =
            HEIGHT;


        const sourceCtx =
            sourceCanvas.getContext(
                '2d',
                {
                    alpha: true
                }
            );


        if (!sourceCtx) {
            return;
        }


        sourceCtx.imageSmoothingEnabled =
            false;


        const img =
            new Image();


        img.decoding =
            'async';


        img.onload =
            function () {

                sourceCtx.clearRect(
                    0,
                    0,
                    WIDTH,
                    HEIGHT
                );


                sourceCtx.drawImage(
                    img,
                    PAD_X,
                    PAD_Y,
                    FLAG_W,
                    FLAG_H
                );


                startFlagAnimation();

            };


        img.src =
            'data:image/svg+xml;charset=utf-8,' +
            encodeURIComponent(svg);


        /* ========================================================
           DRAW FLAG
        ======================================================== */

        function drawFlag(time) {

            const t =
                time * 0.001;


            ctx.clearRect(
                0,
                0,
                WIDTH,
                HEIGHT
            );


            ctx.save();


            ctx.translate(
                PAD_X,
                PAD_Y
            );


            /*
             * Smaller strips create smoother
             * cloth movement while preserving
             * the sharp triangular edge.
             */
            const strip =
                3;


            for (
                let x = 0;
                x < FLAG_W;
                x += strip
            ) {

                const u =
                    x / FLAG_W;


                /*
                 * Pole remains stable.
                 * Free edge receives stronger movement.
                 */
                const strength =
                    Math.pow(
                        u,
                        1.45
                    );


                /*
                 * MAIN WAVE
                 *
                 * SPEED = 4.0
                 * STRENGTH = 34
                 */
                const wave1 =
                    Math.sin(
                        u *
                        Math.PI *
                        1.55
                        -
                        t * 4.0
                    );


                /*
                 * SECONDARY WAVE
                 *
                 * SPEED = 4.0
                 * STRENGTH = 6
                 *
                 * Lower value keeps the cloth
                 * smooth instead of watery.
                 */
                const wave2 =
                    Math.sin(
                        u *
                        Math.PI *
                        3.10
                        -
                        t * 4.0
                        +
                        1.15
                    );


                /*
                 * VERTICAL FABRIC MOVEMENT
                 */
                const yWave =
                    strength *
                    (
                        wave1 * 34 +
                        wave2 * 6
                    );


                /*
                 * FREE-EDGE MOVEMENT
                 *
                 * Also synchronized to 4.0.
                 */
                const xWave =
                    strength *
                    Math.sin(
                        u *
                        Math.PI *
                        1.35
                        -
                        t * 4.0
                        +
                        0.55
                    ) *
                    15;


                /*
                 * FABRIC TILT
                 */
                const slope =
                    Math.cos(
                        u *
                        Math.PI *
                        1.55
                        -
                        t * 4.0
                    ) *
                    strength *
                    0.16

                    +

                    Math.cos(
                        u *
                        Math.PI *
                        3.10
                        -
                        t * 4.0
                        +
                        1.15
                    ) *
                    strength *
                    0.025;


                const sourceWidth =
                    Math.min(
                        strip + 1,
                        FLAG_W - x
                    );


                ctx.save();


                ctx.translate(
                    x + xWave,
                    yWave
                );


                ctx.transform(
                    1,
                    slope,
                    0,
                    1,
                    0,
                    0
                );


                /*
                 * Slight overlap prevents gaps
                 * between individual cloth strips.
                 */
                ctx.drawImage(
                    sourceCanvas,

                    PAD_X + x,
                    0,
                    sourceWidth,
                    HEIGHT,

                    -0.5,
                    0,
                    sourceWidth + 1,
                    HEIGHT
                );


                ctx.restore();

            }


            ctx.restore();


            /*
             * Very subtle fabric shading.
             */
            ctx.save();


            ctx.globalCompositeOperation =
                'source-atop';


            ctx.globalAlpha =
                0.075;


            const shadeGradient =
                ctx.createLinearGradient(
                    PAD_X,
                    0,
                    PAD_X + FLAG_W,
                    0
                );


            shadeGradient.addColorStop(
                0,
                'rgba(0,0,0,0)'
            );


            shadeGradient.addColorStop(
                0.35,
                'rgba(0,0,0,0.18)'
            );


            shadeGradient.addColorStop(
                0.52,
                'rgba(255,255,255,0.10)'
            );


            shadeGradient.addColorStop(
                0.72,
                'rgba(0,0,0,0.15)'
            );


            shadeGradient.addColorStop(
                1,
                'rgba(0,0,0,0)'
            );


            ctx.fillStyle =
                shadeGradient;


            ctx.fillRect(
                PAD_X,
                PAD_Y,
                FLAG_W,
                FLAG_H
            );


            ctx.restore();

        }


        /* ========================================================
           START FLAG ANIMATION
        ======================================================== */

        function startFlagAnimation() {

            function animate(time) {

                drawFlag(time);

                requestAnimationFrame(
                    animate
                );

            }


            requestAnimationFrame(
                animate
            );

        }

    })();


    /* ============================================================
       FLOATING BUTTONS
    ============================================================ */

    const floatingButtons =
        document.querySelectorAll(
            '.floating-btn'
        );


    floatingButtons.forEach(
        function (button) {

            button.addEventListener(
                'mouseenter',
                function () {

                    this.classList.add(
                        'hovered'
                    );

                }
            );


            button.addEventListener(
                'mouseleave',
                function () {

                    this.classList.remove(
                        'hovered'
                    );

                }
            );

        }
    );


    /* ============================================================
       PHONE / EMAIL LINKS
    ============================================================ */

    document
        .querySelectorAll(
            'a[href^="tel:"], ' +
            'a[href^="mailto:"]'
        )
        .forEach(
            function (link) {

                link.addEventListener(
                    'click',
                    function () {

                        closeDrawer();

                    }
                );

            }
        );


    /* ============================================================
       WINDOW RESIZE
    ============================================================ */

    let resizeTimer =
        null;


    window.addEventListener(
        'resize',
        function () {

            clearTimeout(
                resizeTimer
            );


            resizeTimer =
                setTimeout(
                    function () {

                        updateDelivery();

                        updateActiveNavigation();

                    },
                    150
                );

        }
    );


    /* ============================================================
       PAGE READY
    ============================================================ */

    document.documentElement
        .classList.add(
            'js-ready'
        );


})();