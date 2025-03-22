const inputElem = document.getElementById('font-size'); // input要素
const currentValueElem = document.getElementById('current-fontsize'); // 埋め込む先のspan要素

// inputイベント時に値をセットする関数
const rangeOnChange = (e) =>{
    window.document.getElementById('font_sample_textarea').style.fontSize = e.target.value + 'px';
    currentValueElem.innerText = e.target.value;
}

window.onload = () => {
    inputElem.addEventListener('input', rangeOnChange);
    currentValueElem.innerText = inputElem.value;
}

let button_style = null;
// チェックボックスとテキストエリアの要素を取得
document.addEventListener('DOMContentLoaded', function() {
    // チェックボックス要素を取得
    const checkbox = document.getElementById('font-change-all');
    
    // チェックボックスの状態が変わったときのイベントリスナーを設定
    checkbox.addEventListener('change', function() {
      if (this.checked) {
        ff = window.getComputedStyle(document.querySelector('.font_sample_textarea')).fontFamily;
        document.body.style.fontFamily = ff;
        // button要素にも適用
        button_style = document.createElement("style");
        button_style.textContent = "button { font-family: " + ff + "; }";
        document.head.appendChild(button_style);
      } else {
        document.body.style.fontFamily = '';
        document.head.removeChild(button_style);
      }
    });
  });