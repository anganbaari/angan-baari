/* ================================================================
   THE HIMALAYAN SUSTAINABLE FARM — Premium JavaScript
   Features: Loader, Navbar scroll, Carousel, Lightbox,
             ScrollSpy, AOS init, Animated Counters, Mobile Menu
================================================================ */
 
document.addEventListener('DOMContentLoaded', () => {
 
    // ============================================================
    // 1. PAGE LOADER
    // ============================================================
    const loader = document.getElementById('loader');
    if (loader) {
        window.addEventListener('load', () => {
            setTimeout(() => {
                loader.classList.add('hidden');
            }, 1600);
        });
        // Fallback: hide loader after 3s even if load hasn't fired
        setTimeout(() => loader.classList.add('hidden'), 3000);
    }
 
 
    // ============================================================
    // 2. NAVBAR — Scroll effect + sliding active-link pill
    // ============================================================
    const navbar = document.getElementById('navbar');
    if (navbar) {
        if (navbar.dataset.navMode === 'solid') {
            // Interior pages (shop, cart, checkout) want a permanently
            // solid navbar — no hero behind it, so skip the scroll logic.
            navbar.classList.add('scrolled');
        } else {
            const sentinel = document.getElementById('navbar-sentinel');
            if (sentinel && 'IntersectionObserver' in window) {
                // Toggles exactly when the hero scrolls out of view —
                // works the same regardless of hero height, so it behaves
                // consistently on phone, tablet, and desktop alike.
                const io = new IntersectionObserver(
                    ([entry]) => navbar.classList.toggle('scrolled', !entry.isIntersecting),
                    { threshold: 0 }
                );
                io.observe(sentinel);
            } else {
                // Fallback for older browsers without IntersectionObserver
                const onScroll = () => navbar.classList.toggle('scrolled', window.scrollY > 60);
                window.addEventListener('scroll', onScroll, { passive: true });
                onScroll();
            }
        }

        // Sliding pill that highlights the active section link
        const navLinksWrap = navbar.querySelector('.nav-links');
        if (navLinksWrap) {
            let pill = navLinksWrap.querySelector('.nav-pill');
            if (!pill) {
                pill = document.createElement('span');
                pill.className = 'nav-pill';
                navLinksWrap.prepend(pill);
            }
            const linkEls = Array.from(navLinksWrap.querySelectorAll('a:not(.nav-cta)'));

            function movePill(el) {
                if (!el) { pill.style.opacity = '0'; return; }
                pill.style.opacity = '1';
                pill.style.width = el.offsetWidth + 'px';
                pill.style.transform = `translateX(${el.offsetLeft}px)`;
            }

            function currentActive() {
                return linkEls.find(a => a.classList.contains('active'));
            }

            function setActiveByHash() {
                const hash = window.location.hash || linkEls[0]?.getAttribute('href');
                const match = linkEls.find(a => a.getAttribute('href') === hash) || linkEls[0];
                linkEls.forEach(a => a.classList.toggle('active', a === match));
                movePill(match);
            }

            linkEls.forEach(a => {
                a.addEventListener('mouseenter', () => movePill(a));
                a.addEventListener('click', () => setTimeout(setActiveByHash, 60));
            });
            navLinksWrap.addEventListener('mouseleave', () => movePill(currentActive()));
            window.addEventListener('resize', () => movePill(currentActive()));

            setActiveByHash();
        }
    }
 
 
    // ============================================================
    // 3. SIDE DRAWER MENU
    // ============================================================
    const hamburger      = document.getElementById('mobile-menu');
    const sideDrawer     = document.getElementById('sideDrawer');
    const drawerBackdrop = document.getElementById('drawerBackdrop');
    const drawerClose    = document.getElementById('drawerClose');
    const drawerLinks    = document.querySelectorAll('.drawer-link');
    const drawerOrderBtn = document.querySelector('.drawer-order-btn');
 
    function openDrawer() {
        sideDrawer.classList.add('open');
        drawerBackdrop.classList.add('active');
        hamburger.classList.add('open');
        hamburger.setAttribute('aria-expanded', 'true');
        sideDrawer.setAttribute('aria-hidden', 'false');
        document.body.style.overflow = 'hidden';
 
        // Stagger-reveal each nav link
        drawerLinks.forEach((link, i) => {
            link.style.transitionDelay = `${0.12 + i * 0.06}s`;
            setTimeout(() => link.classList.add('revealed'), 10);
        });
    }
 
    function closeDrawer() {
        sideDrawer.classList.remove('open');
        drawerBackdrop.classList.remove('active');
        hamburger.classList.remove('open');
        hamburger.setAttribute('aria-expanded', 'false');
        sideDrawer.setAttribute('aria-hidden', 'true');
        document.body.style.overflow = '';
 
        // Reset stagger for next open
        drawerLinks.forEach(link => {
            link.classList.remove('revealed');
            link.style.transitionDelay = '0s';
        });
    }
 
    if (hamburger) hamburger.addEventListener('click', openDrawer);
    if (drawerClose) drawerClose.addEventListener('click', closeDrawer);
    if (drawerBackdrop) drawerBackdrop.addEventListener('click', closeDrawer);
 
    // Close on any drawer link click
    drawerLinks.forEach(link => link.addEventListener('click', closeDrawer));
    if (drawerOrderBtn) drawerOrderBtn.addEventListener('click', closeDrawer);
 
    // Close on Escape key
    document.addEventListener('keydown', e => {
        if (e.key === 'Escape' && sideDrawer && sideDrawer.classList.contains('open')) {
            closeDrawer();
        }
    });
 
 
    // ============================================================
    // 4. HERO CAROUSEL
    // ============================================================
    const slides       = document.querySelectorAll('.carousel-slide');
    const prevButton   = document.querySelector('.prev-slide');
    const nextButton   = document.querySelector('.next-slide');
    const dotsContainer = document.querySelector('.carousel-dots');
    let currentSlide   = 0;
    let slideInterval;
 
    function showSlide(index) {
        slides[currentSlide].classList.remove('active');
        if (dotsContainer && dotsContainer.children[currentSlide]) {
            dotsContainer.children[currentSlide].classList.remove('active');
        }
 
        if (index >= slides.length) currentSlide = 0;
        else if (index < 0)         currentSlide = slides.length - 1;
        else                        currentSlide = index;
 
        slides[currentSlide].classList.add('active');
        if (dotsContainer && dotsContainer.children[currentSlide]) {
            dotsContainer.children[currentSlide].classList.add('active');
        }
    }
 
    function nextSlide() { showSlide(currentSlide + 1); }
    function prevSlide() { showSlide(currentSlide - 1); }
 
    function createDots() {
        if (!dotsContainer) return;
        slides.forEach((_, i) => {
            const dot = document.createElement('div');
            dot.classList.add('dot');
            if (i === 0) dot.classList.add('active');
            dot.setAttribute('aria-label', `Go to slide ${i + 1}`);
            dot.addEventListener('click', () => {
                showSlide(i);
                resetAutoSlide();
            });
            dotsContainer.appendChild(dot);
        });
    }
 
    function startAutoSlide() {
        slideInterval = setInterval(nextSlide, 5500);
    }
 
    function resetAutoSlide() {
        clearInterval(slideInterval);
        startAutoSlide();
    }
 
    if (nextButton) {
        nextButton.addEventListener('click', () => { nextSlide(); resetAutoSlide(); });
    }
    if (prevButton) {
        prevButton.addEventListener('click', () => { prevSlide(); resetAutoSlide(); });
    }
 
    // Swipe support for mobile
    let touchStartX = 0;
    const heroCarousel = document.querySelector('.hero-carousel');
    if (heroCarousel) {
        heroCarousel.addEventListener('touchstart', e => {
            touchStartX = e.changedTouches[0].screenX;
        }, { passive: true });
        heroCarousel.addEventListener('touchend', e => {
            const diff = touchStartX - e.changedTouches[0].screenX;
            if (Math.abs(diff) > 50) {
                if (diff > 0) { nextSlide(); } else { prevSlide(); }
                resetAutoSlide();
            }
        }, { passive: true });
    }
 
    if (slides.length > 0) {
        createDots();
        showSlide(0);
        startAutoSlide();
    }
 
 
    // ============================================================
    // 5. GALLERY LIGHTBOX + FILTER
    // ============================================================
    const lightbox       = document.getElementById('lightbox');
    const lightboxImg    = document.querySelector('.lightbox-content');
    const lightboxCaption = document.querySelector('.lightbox-caption');
    const closeButton    = document.querySelector('.close-button');
 
    function openLightbox(img) {
        if (!lightbox || !lightboxImg) return;
        lightbox.classList.add('active');
        lightboxImg.src = img.src;
        lightboxImg.alt = img.alt;
        if (lightboxCaption) lightboxCaption.textContent = img.alt;
        document.body.style.overflow = 'hidden';
    }
 
    function closeLightbox() {
        if (!lightbox) return;
        lightbox.classList.remove('active');
        document.body.style.overflow = '';
    }
 
    // Wire lightbox to all masonry gallery images
    document.querySelectorAll('.gm-item img').forEach(img => {
        img.addEventListener('click', () => openLightbox(img));
    });
 
    if (closeButton) {
        closeButton.addEventListener('click', closeLightbox);
        closeButton.addEventListener('keydown', e => {
            if (e.key === 'Enter' || e.key === ' ') closeLightbox();
        });
    }
 
    if (lightbox) {
        lightbox.addEventListener('click', e => {
            if (e.target === lightbox) closeLightbox();
        });
    }
 
    document.addEventListener('keydown', e => {
        if (e.key === 'Escape' && lightbox && lightbox.classList.contains('active')) {
            closeLightbox();
        }
    });
 
    // Gallery Filter
    const filterBtns    = document.querySelectorAll('.gf-btn');
    const galleryItems  = document.querySelectorAll('.gm-item');
    const visibleCount  = document.getElementById('visibleCount');
    const totalCount    = document.getElementById('totalCount');
    const totalPhotos   = galleryItems.length;
 
    if (totalCount) totalCount.textContent = totalPhotos;
    if (visibleCount) visibleCount.textContent = totalPhotos;
 
    filterBtns.forEach(btn => {
        btn.addEventListener('click', () => {
            // Update active button
            filterBtns.forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
 
            const filter = btn.dataset.filter;
            let count = 0;
 
            galleryItems.forEach(item => {
                const show = filter === 'all' || item.dataset.cat === filter;
                item.classList.toggle('hidden', !show);
                if (show) count++;
            });
 
            if (visibleCount) visibleCount.textContent = count;
        });
    });
 
 
    // ============================================================
    // 6. SHOP — CATEGORY FILTER
    // ============================================================
    const shopTabs    = document.querySelectorAll('.shop-tab');
    const shopCards   = document.querySelectorAll('.shop-card');
    const shopVisible = document.getElementById('shopVisible');
    const shopTotal   = document.getElementById('shopTotal');
 
    if (shopTotal) shopTotal.textContent = shopCards.length;
    if (shopVisible) shopVisible.textContent = shopCards.length;
 
    shopTabs.forEach(tab => {
        tab.addEventListener('click', () => {
            // Update active tab
            shopTabs.forEach(t => t.classList.remove('active'));
            tab.classList.add('active');
 
            const cat = tab.dataset.cat;
            let count = 0;
 
            shopCards.forEach(card => {
                const show = cat === 'all' || card.dataset.cat === cat;
                card.classList.toggle('hidden', !show);
                if (show) count++;
            });
 
            if (shopVisible) shopVisible.textContent = count;
        });
    });
 
    // Mark out-of-stock cards for dimmed styling
    shopCards.forEach(card => {
        const stockBadge = card.querySelector('.shop-stock');
        if (stockBadge && stockBadge.classList.contains('out-of-stock')) {
            card.classList.add('out-of-stock-card');
        }
    });
 
 
    // ============================================================
    // 7. SCROLLSPY — Active nav link highlighting
    // ============================================================
    const sections   = document.querySelectorAll('section[id], div[id]');
    const navAnchors = document.querySelectorAll('.nav-links a[href^="#"], .drawer-link[href^="#"]');
 
    const scrollSpy = () => {
        const scrollPos = window.scrollY + 120;
        sections.forEach(sec => {
            if (scrollPos >= sec.offsetTop && scrollPos < sec.offsetTop + sec.offsetHeight) {
                navAnchors.forEach(link => {
                    link.classList.remove('active');
                    if (link.getAttribute('href') === `#${sec.id}`) {
                        link.classList.add('active');
                    }
                });
            }
        });
    };
 
   window.addEventListener('scroll', () => {
    scrollSpy();
    const active = navLinksWrap?.querySelector('.nav-links a.active');
    if (active) movePill(active);
}, { passive: true });
 
    // ============================================================
    // 8. ANIMATED COUNTERS
    // ============================================================
    const statNumbers = document.querySelectorAll('.stat-number');
 
    function animateCounter(el) {
        const target = parseInt(el.getAttribute('data-target'), 10);
        if (isNaN(target) || target === 0) return; // skip zero
        const duration = 1800;
        const start = performance.now();
 
        function update(now) {
            const elapsed = now - start;
            const progress = Math.min(elapsed / duration, 1);
            // Ease out cubic
            const eased = 1 - Math.pow(1 - progress, 3);
            el.textContent = Math.floor(eased * target);
            if (progress < 1) requestAnimationFrame(update);
            else el.textContent = target;
        }
        requestAnimationFrame(update);
    }
 
    // Trigger counters when stats strip enters view
    const statsStrip = document.querySelector('.stats-strip');
    if (statsStrip && statNumbers.length) {
        let counted = false;
        const observer = new IntersectionObserver(entries => {
            if (entries[0].isIntersecting && !counted) {
                counted = true;
                statNumbers.forEach(el => animateCounter(el));
            }
        }, { threshold: 0.4 });
        observer.observe(statsStrip);
    }
 
 
    // ============================================================
    // 9. ORDER FORM SUBMISSION
    // ============================================================

    const orderForm = document.getElementById('order-form');

    if (orderForm) {
        orderForm.addEventListener('submit', function() {
            
            const btn = this.querySelector('.fs-btn-submit');

            if (!btn) return;

            btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Sending...';
            btn.disabled = true;

            // No preventDefault()
            // Django receives the form normally
        });

    }
 
    // ============================================================
    // 10. AOS (Animate On Scroll) Initialization
    // ============================================================
    if (typeof AOS !== 'undefined') {
        AOS.init({
            duration: 800,
            easing: 'ease-out-cubic',
            once: true,
            offset: 60,
            disableMutationObserver: false,
        });
    }

    // Force contact form visible after AOS init
    setTimeout(function() {
        const contactForm = document.querySelector('.contact-form-wrap');
        if (contactForm) {
            contactForm.style.opacity = '1';
            contactForm.style.visibility = 'visible';
            contactForm.style.transform = 'none';
            contactForm.classList.add('aos-animate');
        }
        const contactTwo = document.querySelector('.contact-two-col');
        if (contactTwo) {
            contactTwo.style.opacity = '1';
            contactTwo.style.visibility = 'visible';
            contactTwo.style.transform = 'none';
        }
    }, 100);
 
 
    // ============================================================
    // 11. SMOOTH SCROLL for anchor links
    // ============================================================
    document.querySelectorAll('a[href^="#"]').forEach(anchor => {
        anchor.addEventListener('click', function(e) {
            const targetId = this.getAttribute('href');
            if (targetId === '#') return;
            const target = document.querySelector(targetId);
            if (target) {
                e.preventDefault();
                const offset = navbar ? navbar.offsetHeight + 16 : 80;
                const top = target.getBoundingClientRect().top + window.scrollY - offset;
                window.scrollTo({ top, behavior: 'smooth' });
            }
        });
    });
 
}); // end DOMContentLoaded

// ============================================================
// 12. INFINITE FARM BOARD CAROUSEL
// ============================================================
document.addEventListener('DOMContentLoaded', () => {

    const fbTrack    = document.getElementById('fbTrack');
    const fbViewport = document.getElementById('fbViewport');
    const fbDotsWrap = document.getElementById('fbDots');
    const fbProgFill = document.getElementById('fbProgFill');
    const fbPauseBtn = document.getElementById('fbPauseBtn');
    const fbPauseIco = document.getElementById('fbPauseIco');
    const fbPauseTxt = document.getElementById('fbPauseTxt');

    if (!fbTrack || !fbViewport) return;

    const FB_DATA = [
        { icon:'fa-leaf',    cls:'fb-icon-now',  st:'fb-s-now',  lbl:'Growing now',    name:'Organic Mangoes',      desc:'Peak season · May–July' },
        { icon:'fa-carrot',  cls:'fb-icon-now',  st:'fb-s-now',  lbl:'Growing now',    name:'Seasonal Vegetables',  desc:'Fresh harvest weekly' },
        { icon:'fa-clock',   cls:'fb-icon-soon', st:'fb-s-soon', lbl:'Coming soon',    name:'Litchi',               desc:'Expected · June 2026' },
        { icon:'fa-jar',     cls:'fb-icon-done', st:'fb-s-done', lbl:'Just harvested', name:'AnganBaari Gold Honey', desc:'Limited stock available' },
        { icon:'fa-leaf',    cls:'fb-icon-now',  st:'fb-s-now',  lbl:'Growing now',    name:'Papaya & Bananas',     desc:'Year-round · order anytime' },
        { icon:'fa-clock',   cls:'fb-icon-soon', st:'fb-s-soon', lbl:'Coming soon',    name:'Mushrooms',            desc:'Expected · Ashadh 2083' },
        { icon:'fa-paw',     cls:'fb-icon-now',  st:'fb-s-now',  lbl:'Growing now',    name:'Free-range Hens',      desc:'Available year-round' },
    ];

    const FB_TOTAL    = FB_DATA.length;
    const FB_CARD_W   = 210;
    const FB_GAP      = 18;
    const FB_STEP     = FB_CARD_W + FB_GAP;
    const FB_DURATION = 3000;
    const FB_VISIBLE  = 9;
    const FB_CENTER   = Math.floor(FB_VISIBLE / 2);

    let fbPaused    = false;
    let fbRafId     = null;
    let fbProgStart = null;
    let fbDataPtr   = 0;
    let fbTrackX    = 0;
    let fbSlots     = [];

    function fbMakeCard(dataIndex) {
        const d = FB_DATA[((dataIndex % FB_TOTAL) + FB_TOTAL) % FB_TOTAL];
        const el = document.createElement('div');
        el.className = 'fb-card';
        el.innerHTML = `
            <div class="fb-icon ${d.cls}"><i class="fas ${d.icon}"></i></div>
            <p class="fb-status ${d.st}">${d.lbl}</p>
            <p class="fb-name">${d.name}</p>
            <p class="fb-desc">${d.desc}</p>`;
        return el;
    }

    // Initial render
    for (let i = 0; i < FB_VISIBLE; i++) {
        const di = fbDataPtr - FB_CENTER + i;
        const el = fbMakeCard(di);
        fbTrack.appendChild(el);
        fbSlots.push({ el, dataIndex: di });
    }

    // Dots
    for (let i = 0; i < FB_TOTAL; i++) {
        const d = document.createElement('button');
        d.className = 'fb-dot';
        d.setAttribute('aria-label', FB_DATA[i].name);
        d.addEventListener('click', () => { fbGoTo(i); fbResetProg(); });
        fbDotsWrap.appendChild(d);
    }

    function fbGetDots() { return fbDotsWrap.querySelectorAll('.fb-dot'); }
    function fbGetVpW()  { return fbViewport.offsetWidth || 900; }

    function fbApplyTrackX(x, animate) {
        if (animate) fbTrack.classList.add('fb-animating');
        else fbTrack.classList.remove('fb-animating');
        fbTrack.style.transform = `translateX(${x}px)`;
    }

    function fbApplyClasses() {
        fbSlots.forEach((s, i) => {
            s.el.classList.remove('fb-active','fb-near','fb-mid');
            const dist = Math.abs(i - FB_CENTER);
            if      (dist === 0) s.el.classList.add('fb-active');
            else if (dist === 1) s.el.classList.add('fb-near');
            else if (dist === 2) s.el.classList.add('fb-mid');
        });
        const realDot = ((fbDataPtr % FB_TOTAL) + FB_TOTAL) % FB_TOTAL;
        fbGetDots().forEach((d, i) => d.classList.toggle('fb-dot-active', i === realDot));
    }

    function fbCalcX() {
        const vpW = fbGetVpW();
        return (vpW / 2) - (FB_CENTER * FB_STEP) - (FB_CARD_W / 2);
    }

    function fbGoTo(realI) {
        fbDataPtr = realI;
        fbTrack.innerHTML = '';
        fbSlots = [];
        for (let i = 0; i < FB_VISIBLE; i++) {
            const di = fbDataPtr - FB_CENTER + i;
            const el = fbMakeCard(di);
            fbTrack.appendChild(el);
            fbSlots.push({ el, dataIndex: di });
        }
        fbTrackX = fbCalcX();
        fbApplyTrackX(fbTrackX, false);
        fbApplyClasses();
    }

    function fbAdvance() {
        fbDataPtr++;
        fbTrackX -= FB_STEP;
        fbApplyTrackX(fbTrackX, true);
        fbApplyClasses();

        setTimeout(() => {
            fbTrack.classList.remove('fb-animating');
            const removed = fbSlots.shift();
            fbTrack.removeChild(removed.el);
            const newDi = fbSlots[fbSlots.length - 1].dataIndex + 1;
            const newEl = fbMakeCard(newDi);
            fbTrack.appendChild(newEl);
            fbSlots.push({ el: newEl, dataIndex: newDi });
            fbTrackX += FB_STEP;
            fbTrack.style.transform = `translateX(${fbTrackX}px)`;
            fbApplyClasses();
        }, 730);
    }

    function fbStartProg() {
        fbProgStart = performance.now();
        if (fbProgFill) fbProgFill.style.width = '0%';

        function tick(now) {
            if (fbPaused) return;
            const pct = Math.min(((now - fbProgStart) / FB_DURATION) * 100, 100);
            if (fbProgFill) fbProgFill.style.width = pct.toFixed(1) + '%';
            if (pct < 100) {
                fbRafId = requestAnimationFrame(tick);
            } else {
                fbAdvance();
                fbRafId = requestAnimationFrame(() => fbStartProg());
            }
        }
        fbRafId = requestAnimationFrame(tick);
    }

    function fbStopProg()  { cancelAnimationFrame(fbRafId); }
    function fbResetProg() { fbStopProg(); if (!fbPaused) fbStartProg(); }

    if (fbPauseBtn) {
        fbPauseBtn.addEventListener('click', () => {
            fbPaused = !fbPaused;
            if (fbPaused) {
                fbStopProg();
                if (fbPauseIco) fbPauseIco.className = 'fas fa-play';
                if (fbPauseTxt) fbPauseTxt.textContent = 'Resume';
            } else {
                if (fbPauseIco) fbPauseIco.className = 'fas fa-pause';
                if (fbPauseTxt) fbPauseTxt.textContent = 'Pause';
                fbStartProg();
            }
        });
    }

    // Init
    fbTrackX = fbCalcX();
    fbApplyTrackX(fbTrackX, false);
    fbApplyClasses();
    fbStartProg();

    window.addEventListener('resize', () => {
        fbTrackX = fbCalcX();
        fbApplyTrackX(fbTrackX, false);
    }, { passive: true });

});
// ============================================================
// BACK TO TOP BUTTON
// ============================================================
document.addEventListener('DOMContentLoaded', () => {
    const backToTop = document.getElementById('backToTop');
    if (!backToTop) return;

    // Show button after scrolling 400px
    window.addEventListener('scroll', () => {
        if (window.scrollY > 400) {
            backToTop.classList.add('visible');
        } else {
            backToTop.classList.remove('visible');
        }
    }, { passive: true });

    // Smooth scroll to top on click
    backToTop.addEventListener('click', () => {
        window.scrollTo({ top: 0, behavior: 'smooth' });
    });
});

// ================================================================
// NEWSLETTER POPUP — shows after 30 seconds
// ================================================================
document.addEventListener('DOMContentLoaded', function () {
    const popup     = document.getElementById('nlPopup');
    const backdrop  = document.getElementById('nlBackdrop');
    const closeBtn  = document.getElementById('nlClose');
    const noThanks  = document.getElementById('nlNoThanks');
    const popupForm = document.getElementById('nlPopupForm');

    if (!popup) return;

    // Don't show again for 24 hours after dismissal
    const dismissedUntil = localStorage.getItem('nl_dismissed_until');
    if (dismissedUntil && Date.now() < Number(dismissedUntil)) return;

    function openPopup() {
        popup.classList.add('active');
        if (backdrop) backdrop.classList.add('active');
        document.body.style.overflow = 'hidden';
    }

    function closePopup() {
        popup.classList.remove('active');
        if (backdrop) backdrop.classList.remove('active');
        document.body.style.overflow = '';
        // Suppress for 24 hours
        const expires = Date.now() + 24 * 60 * 60 * 1000;
        localStorage.setItem('nl_dismissed_until', expires);
    }

    // Show after 30 seconds
    setTimeout(openPopup, 30000);

    if (closeBtn)  closeBtn.addEventListener('click', closePopup);
    if (noThanks)  noThanks.addEventListener('click', closePopup);
    if (backdrop)  backdrop.addEventListener('click', closePopup);

    // Close on Escape key
    document.addEventListener('keydown', function (e) {
        if (e.key === 'Escape' && popup.classList.contains('active')) {
            closePopup();
        }
    });

    // Handle popup form submit
    if (popupForm) {
        popupForm.addEventListener('submit', function () {
            closePopup();
        });
    }
});

// ================================================================
// NEWSLETTER SECTION — show success message after submit
// ================================================================
document.addEventListener('DOMContentLoaded', function () {
    const form    = document.getElementById('newsletterForm');
    const success = document.getElementById('newsletterSuccess');

    if (!form) return;

    // Check if we just came back from a newsletter signup
    const params = new URLSearchParams(window.location.search);
    if (params.get('subscribed') === '1') {
        if (form) form.style.display = 'none';
        if (success) success.classList.add('show');
        // Scroll to newsletter section smoothly after a short delay
        const section = document.getElementById('newsletter');
        if (section) {
            setTimeout(() => section.scrollIntoView({ behavior: 'smooth' }), 300);
        }
    }
});

// ================================================================
// CONTACT FORM — AJAX submit (no page refresh)
// ================================================================
(function() {
    const form    = document.getElementById('contactForm');
    const success = document.getElementById('contactSuccess');
    const btn     = form ? form.querySelector('button[type="submit"]') : null;

    if (!form) return;

    form.addEventListener('submit', function(e) {
        e.preventDefault();

        // Change button to loading state
        btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Sending...';
        btn.disabled = true;

        const formData = new FormData(form);

        fetch(form.action, {
            method: 'POST',
            body: formData,
            headers: {
                'X-Requested-With': 'XMLHttpRequest',
            }
        })
        .then(res => res.json())
        .then(data => {
            if (data.status === 'success') {
                // Show success message
                if (success) success.style.display = 'block';
                // Clear all fields
                form.querySelectorAll('input, textarea').forEach(el => el.value = '');
                // Reset button
                btn.innerHTML = '<i class="fas fa-check"></i> Message Sent!';
                btn.style.background = 'var(--moss)';
                // After 3 seconds reset button back to normal
                setTimeout(() => {
                    btn.innerHTML = '<i class="fas fa-paper-plane"></i> Send Message';
                    btn.style.background = 'var(--gold)';
                    btn.disabled = false;
                    // Hide success after 5 seconds
                    if (success) success.style.display = 'none';
                }, 5000);
            } else {
                // Show error
                btn.innerHTML = '<i class="fas fa-exclamation-circle"></i> Please fill all fields!';
                btn.style.background = '#c0392b';
                setTimeout(() => {
                    btn.innerHTML = '<i class="fas fa-paper-plane"></i> Send Message';
                    btn.style.background = 'var(--gold)';
                    btn.disabled = false;
                }, 3000);
            }
        })
        .catch(() => {
            btn.innerHTML = '<i class="fas fa-exclamation-circle"></i> Something went wrong!';
            btn.style.background = '#c0392b';
            setTimeout(() => {
                btn.innerHTML = '<i class="fas fa-paper-plane"></i> Send Message';
                btn.style.background = 'var(--gold)';
                btn.disabled = false;
            }, 3000);
        });
    });
})();